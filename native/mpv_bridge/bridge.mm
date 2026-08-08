#import <Cocoa/Cocoa.h>
#import <OpenGL/gl3.h>
#import <OpenGL/OpenGL.h>

#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <mutex>
#include <string>
#include <pthread/qos.h>

#include "mpv/client.h"
#include "mpv/render.h"
#include "mpv/render_gl.h"

// Every diagnostic in this file used to go to stderr, which a bundled .app
// discards -- so nothing the bridge knows (load failures, transition holds,
// per-view present skips) ever reached singws_*.log, and an output window that
// went blank left no evidence at all. Route through a host-installed callback
// when there is one, and keep stderr for the unbundled/dev case.
typedef void (*SingWSBridgeLogFn)(const char *);
static std::atomic<SingWSBridgeLogFn> g_bridgeLog{nullptr};

static void bridgeLog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void bridgeLog(const char *fmt, ...) {
    char line[1024];
    va_list args;
    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    // mpv's own log lines already end in a newline; the host log adds its own.
    for (size_t n = strlen(line); n && (line[n-1] == '\n' || line[n-1] == '\r'); --n)
        line[n-1] = '\0';
    SingWSBridgeLogFn sink = g_bridgeLog.load();
    if (sink) sink(line); else fprintf(stderr, "%s\n", line);
}

@class BridgeRenderer;

@interface BridgeVideoView : NSOpenGLView
@property(nonatomic, weak) BridgeRenderer *renderer;
@end

@interface BridgeRenderer : NSObject
- (instancetype)initWithOutput:(NSView *)output preview:(NSView *)preview;
- (BOOL)loadVideo:(NSString *)video audio:(NSString *)audio;
- (void)presentView:(BridgeVideoView *)view;
- (void)scheduleRender;
- (void)scheduleEvents;
- (void)shutdown;
- (void)setPaused:(BOOL)paused;
- (void)stopPlayback;
- (void)seekMilliseconds:(int64_t)milliseconds;
- (int64_t)positionMilliseconds;
- (int64_t)durationMilliseconds;
- (BOOL)isPlaying;
- (BOOL)atEnd;
- (BOOL)visualReady;
- (void)setVolumePercent:(double)value;
- (void)setAudioDeviceName:(NSString *)name;
- (void)setTempoPercent:(int)percent;
- (void)setSemitones:(int)semitones;
- (void)setDspChain:(const char *)chain;
- (void)setAudioDelaySeconds:(double)seconds;
- (void)setCdgSidefill:(BOOL)enabled;
- (void)beginWindowTransition:(int)durationMs;
@end

@implementation BridgeVideoView
- (void)drawRect:(NSRect)dirty { (void)dirty; [self.renderer presentView:self]; }
- (void)mouseDown:(NSEvent *)event {
    // The Qt app owns a same-Space borderless presentation mode. Calling
    // AppKit toggleFullScreen here entered a different native Space and could
    // strand a static CDG view there. Forward the click into Qt's host NSView
    // so VideoWindow.toggle_fullscreen() is the one authoritative path.
    if (event.clickCount == 2 && self.superview) {
        [self.superview mouseDown:event];
        return;
    }
    [super mouseDown:event];
}
@end

static void *getProc(void *ctx, const char *name) { (void)ctx; return dlsym(RTLD_DEFAULT, name); }
static void renderWake(void *ctx) {
    @autoreleasepool { [(__bridge BridgeRenderer *)ctx scheduleRender]; }
}
static void eventWake(void *ctx) {
    @autoreleasepool { [(__bridge BridgeRenderer *)ctx scheduleEvents]; }
}

// Reusable scan-only libmpv core. It has no window and no audio device: the
// null output runs untimed, so decoding is as fast as the CPU/storage allow.
// Queue orchestration calls this only while karaoke is idle. One mutex keeps
// the single core deterministic even if two preparation requests overlap.
static std::mutex gScannerMutex;
static std::atomic<mpv_handle *> gScannerHandle{nullptr};
static std::atomic_bool gScannerCancel{false};

static mpv_handle *makeSilenceScanner(void) {
    mpv_handle *h=mpv_create();
    if(!h)return nullptr;
    const char *options[][2]={
        {"config","no"},{"terminal","no"},{"input-default-bindings","no"},
        {"load-scripts","no"},{"vid","no"},{"audio-display","no"},
        {"ao","null"},{"ao-null-untimed","yes"},{"keep-open","no"},
        {"idle","yes"},{"pause","no"},{"audio-pitch-correction","no"},
    };
    for(const auto &option:options){
        int r=mpv_set_option_string(h,option[0],option[1]);
        if(r<0){
            bridgeLog("[scanner] option %s failed: %s",option[0],mpv_error_string(r));
            mpv_terminate_destroy(h); return nullptr;
        }
    }
    int r=mpv_initialize(h);
    if(r<0){
        bridgeLog("[scanner] initialize failed: %s",mpv_error_string(r));
        mpv_terminate_destroy(h); return nullptr;
    }
    // mpv maps libavfilter's AV_LOG_INFO messages to its "v" level.
    // silencedetect markers are therefore invisible at mpv's "info" level.
    mpv_request_log_messages(h,"v");
    return h;
}

static bool parseSilenceValue(const char *text,const char *marker,double *value){
    const char *p=text?strstr(text,marker):nullptr;
    if(!p)return false;
    p+=strlen(marker); char *end=nullptr; double parsed=strtod(p,&end);
    if(end==p||!std::isfinite(parsed))return false;
    *value=parsed; return true;
}

static int scanSilence(const char *path,double noiseDb,double leadMinimum,
                       double trailMinimum,double *leadOut,double *trailOut,
                       double *durationOut){
    if(leadOut)*leadOut=0.0;
    if(trailOut)*trailOut=0.0;
    if(durationOut)*durationOut=0.0;
    if(!path||!*path||!leadOut||!trailOut||!durationOut)return 0;

    // The caller is a dedicated short-lived queue worker. Utility QoS keeps
    // even an unusually expensive decode below UI/audio work on older Macs.
    pthread_set_qos_class_self_np(QOS_CLASS_UTILITY,0);

    std::lock_guard<std::mutex> guard(gScannerMutex);
    gScannerCancel=false;
    mpv_handle *h=gScannerHandle.load();
    if(!h){
        h=makeSilenceScanner();
        if(!h)return 0;
        gScannerHandle.store(h);
    }

    const double detectorMinimum=std::max(0.05,std::min(leadMinimum,trailMinimum));
    char filter[192];
    snprintf(filter,sizeof(filter),
             "lavfi=[silencedetect=noise=%.3fdB:d=%.3f]",noiseDb,detectorMinimum);
    int r=mpv_set_property_string(h,"af",filter);
    if(r<0){
        bridgeLog("[scanner] silencedetect unavailable: %s",mpv_error_string(r));
        return 0;
    }

    // Drain leftovers from the previous completed scan before replacing it.
    while(true){
        mpv_event *old=mpv_wait_event(h,0);
        if(!old||old->event_id==MPV_EVENT_NONE)break;
    }
    const char *cmd[]={"loadfile",path,"replace",nullptr};
    r=mpv_command(h,cmd);
    if(r<0){
        bridgeLog("[scanner] load failed: %s",mpv_error_string(r));
        return 0;
    }

    double duration=0.0,firstStart=-1.0,firstEnd=-1.0;
    double activeStart=-1.0,lastStart=-1.0,lastEnd=-1.0;
    bool loaded=false,finished=false,cleanEof=false;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(30);
    while(!finished&&!gScannerCancel.load()&&std::chrono::steady_clock::now()<deadline){
        mpv_event *event=mpv_wait_event(h,0.10);
        if(!event||event->event_id==MPV_EVENT_NONE)continue;
        if(event->event_id==MPV_EVENT_FILE_LOADED){
            loaded=true;
            mpv_get_property(h,"duration",MPV_FORMAT_DOUBLE,&duration);
        }else if(event->event_id==MPV_EVENT_LOG_MESSAGE){
            mpv_event_log_message *message=(mpv_event_log_message *)event->data;
            double value=0.0;
            if(parseSilenceValue(message?message->text:nullptr,"silence_start:",&value)){
                activeStart=value;
                if(firstStart<0.0)firstStart=value;
            }else if(parseSilenceValue(message?message->text:nullptr,"silence_end:",&value)){
                if(activeStart>=0.0){
                    if(firstEnd<0.0&&firstStart==activeStart)firstEnd=value;
                    lastStart=activeStart; lastEnd=value; activeStart=-1.0;
                }
            }
        }else if(event->event_id==MPV_EVENT_END_FILE){
            mpv_event_end_file *end=(mpv_event_end_file *)event->data;
            cleanEof=end&&end->reason==MPV_END_FILE_REASON_EOF;
            finished=true;
        }
    }

    if(!finished||gScannerCancel.load()){
        const char *stop[]={"stop",nullptr}; mpv_command(h,stop);
        bridgeLog("[scanner] scan cancelled or timed out: %s",path);
        return 0;
    }
    if(!loaded||!cleanEof||duration<=0.0){
        bridgeLog("[scanner] scan did not reach a clean EOF: %s",path);
        return 0;
    }
    if(activeStart>=0.0){
        if(firstEnd<0.0&&firstStart==activeStart)firstEnd=duration;
        lastStart=activeStart; lastEnd=duration;
    }

    double lead=0.0;
    if(firstStart>=0.0&&firstStart<=0.30&&firstEnd>=0.0&&
       (firstEnd-firstStart)>=leadMinimum){
        lead=std::max(0.0,std::min(10.0,firstEnd));
    }
    double trail=duration;
    if(lastStart>=0.0&&lastEnd>=duration-0.70&&
       (lastEnd-lastStart)>=trailMinimum){
        trail=std::max(0.0,std::min(duration,lastStart));
    }
    *leadOut=lead; *trailOut=trail; *durationOut=duration;
    bridgeLog("[scanner] ready lead=%.3f trail=%.3f duration=%.3f %s",
            lead,trail,duration,path);
    return 1;
}

static void shutdownSilenceScanner(void){
    gScannerCancel=true;
    mpv_handle *active=gScannerHandle.load();
    if(active)mpv_wakeup(active);
    std::lock_guard<std::mutex> guard(gScannerMutex);
    active=gScannerHandle.exchange(nullptr);
    if(active)mpv_terminate_destroy(active);
}

static GLuint compileShader(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint ok = GL_FALSE; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[2048] = {0}; glGetShaderInfoLog(shader, sizeof(log), nullptr, log);
        bridgeLog("[bridge] shader error: %s", log);
        glDeleteShader(shader); return 0;
    }
    return shader;
}

static GLuint makeProgram(void) {
    const char *vs =
        "#version 150 core\n"
        "uniform vec2 scale; uniform vec2 uvScale; uniform vec2 uvOffset; out vec2 uv;\n"
        "void main(){ vec2 p[4]=vec2[4](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(1,1));"
        "vec2 t[4]=vec2[4](vec2(0,0),vec2(1,0),vec2(0,1),vec2(1,1));"
        "gl_Position=vec4(p[gl_VertexID]*scale,0,1); uv=t[gl_VertexID]*uvScale+uvOffset; }\n";
    const char *fs =
        "#version 150 core\n"
        "uniform sampler2D frameTexture; uniform int cdgSidefill; in vec2 uv; out vec4 color;"
        "void main(){"
        "if(cdgSidefill!=0){"
        // Preserve the complete 4:3 CDG image that libmpv placed between
        // x=.125 and x=.875. For the side panels, infer the current CDG border
        // color from four points safely inside its 6px/12px protected border.
        // Choosing the medoid makes one stray logo/title corner harmless and,
        // unlike extending the visible edge row-by-row, cannot turn artwork
        // touching that edge into a horizontal streak.
        "float panel=0.125; float right=0.875;"
        "if(uv.x<panel||uv.x>right){"
        "float lx=panel+(right-panel)*(3.0/300.0);"
        "float rx=panel+(right-panel)*(297.0/300.0);"
        "float ty=6.0/216.0; float by=210.0/216.0;"
        "vec4 c0=texture(frameTexture,vec2(lx,ty));"
        "vec4 c1=texture(frameTexture,vec2(rx,ty));"
        "vec4 c2=texture(frameTexture,vec2(lx,by));"
        "vec4 c3=texture(frameTexture,vec2(rx,by));"
        "float s0=distance(c0.rgb,c1.rgb)+distance(c0.rgb,c2.rgb)+distance(c0.rgb,c3.rgb);"
        "float s1=distance(c1.rgb,c0.rgb)+distance(c1.rgb,c2.rgb)+distance(c1.rgb,c3.rgb);"
        "float s2=distance(c2.rgb,c0.rgb)+distance(c2.rgb,c1.rgb)+distance(c2.rgb,c3.rgb);"
        "float s3=distance(c3.rgb,c0.rgb)+distance(c3.rgb,c1.rgb)+distance(c3.rgb,c2.rgb);"
        "vec4 bg=c0; float best=s0;"
        "if(s1<best){bg=c1;best=s1;} if(s2<best){bg=c2;best=s2;} if(s3<best){bg=c3;}"
        "color=bg; return;"
        "}"
        "} color=texture(frameTexture,uv); }\n";
    GLuint v=compileShader(GL_VERTEX_SHADER,vs), f=compileShader(GL_FRAGMENT_SHADER,fs);
    if (!v || !f) return 0;
    GLuint p=glCreateProgram(); glAttachShader(p,v); glAttachShader(p,f); glLinkProgram(p);
    glDeleteShader(v); glDeleteShader(f);
    GLint ok=GL_FALSE; glGetProgramiv(p,GL_LINK_STATUS,&ok);
    if (!ok) { char log[2048]={0}; glGetProgramInfoLog(p,sizeof(log),nullptr,log);
        bridgeLog("[bridge] link error: %s",log); glDeleteProgram(p); return 0; }
    return p;
}

@implementation BridgeRenderer {
    NSOpenGLPixelFormat *_format;
    NSOpenGLContext *_master;
    BridgeVideoView *_outputView, *_previewView;
    GLuint _texture, _fbo, _program, _outVao, _prevVao;
    GLint _scaleUniform, _uvScaleUniform, _uvOffsetUniform, _textureUniform, _sidefillUniform;
    int _width, _height;
    BOOL _hasFrame, _isCdg, _cdgSidefill;
    // Last presentView skip reason per view; -1 so the first pass always logs.
    int _lastOutputSkip, _lastPreviewSkip;
    BOOL _loading, _playWhenLoaded;
    int _desiredTempoPercent, _desiredSemitones;
    // SingWS DSP stages (normalize/EQ/master bus) as an mpv filter string,
    // composed with key into "af" by applyAudioFilters.
    std::string _dspChain;
    // CDG visual-timing calibration, in seconds. Held here and re-applied on
    // every load like tempo/key, because loadfile resets it.
    double _desiredAudioDelay;
    uint64_t _loadSerial;
    std::atomic_bool _outputTransitioning;
    std::atomic<uint64_t> _transitionSerial;
    mpv_handle *_mpv;
    mpv_render_context *_render;
    std::atomic_bool _renderQueued, _eventQueued, _stopping;
}

- (instancetype)initWithOutput:(NSView *)output preview:(NSView *)preview {
    if ((self=[super init])) {
        _width=1920; _height=1080; _renderQueued=false; _eventQueued=false; _stopping=false;
        _lastOutputSkip=-1; _lastPreviewSkip=-1;
        _loading=NO; _playWhenLoaded=NO;
        _desiredTempoPercent=100; _desiredSemitones=0; _loadSerial=0;
        _desiredAudioDelay=0.0;
        _outputTransitioning=false; _transitionSerial=0;
        NSOpenGLPixelFormatAttribute attrs[]={NSOpenGLPFAOpenGLProfile,NSOpenGLProfileVersion3_2Core,
            NSOpenGLPFAAccelerated,NSOpenGLPFADoubleBuffer,NSOpenGLPFAColorSize,24,NSOpenGLPFAAlphaSize,8,0};
        _format=[[NSOpenGLPixelFormat alloc] initWithAttributes:attrs];
        _master=[[NSOpenGLContext alloc] initWithFormat:_format shareContext:nil];
        if (!_format || !_master) return nil;
        [_master makeCurrentContext]; CGLLockContext(_master.CGLContextObj);
        glGenTextures(1,&_texture); glBindTexture(GL_TEXTURE_2D,_texture);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,_width,_height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        glGenFramebuffers(1,&_fbo); glBindFramebuffer(GL_FRAMEBUFFER,_fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,_texture,0);
        GLenum status=glCheckFramebufferStatus(GL_FRAMEBUFFER); glBindFramebuffer(GL_FRAMEBUFFER,0);
        _program=makeProgram();
        _scaleUniform=glGetUniformLocation(_program,"scale");
        _uvScaleUniform=glGetUniformLocation(_program,"uvScale");
        _uvOffsetUniform=glGetUniformLocation(_program,"uvOffset");
        _textureUniform=glGetUniformLocation(_program,"frameTexture");
        _sidefillUniform=glGetUniformLocation(_program,"cdgSidefill");
        CGLUnlockContext(_master.CGLContextObj);
        if (status!=GL_FRAMEBUFFER_COMPLETE || !_program) return nil;
        _outputView=[self attachTo:output]; _previewView=[self attachTo:preview];
        if (!_outputView || !_previewView) return nil;
    }
    return self;
}

- (BridgeVideoView *)attachTo:(NSView *)parent {
    if (!parent) return nil;
    BridgeVideoView *view=[[BridgeVideoView alloc] initWithFrame:parent.bounds pixelFormat:_format];
    NSOpenGLContext *ctx=[[NSOpenGLContext alloc] initWithFormat:_format shareContext:_master];
    [view setOpenGLContext:ctx]; view.renderer=self;
    view.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
    [parent addSubview:view positioned:NSWindowBelow relativeTo:nil];
    return view;
}

- (BOOL)setOption:(const char *)name value:(const char *)value {
    int r=mpv_set_option_string(_mpv,name,value);
    if (r<0) bridgeLog("[bridge] option %s failed: %s",name,mpv_error_string(r));
    return r>=0;
}

- (BOOL)loadVideo:(NSString *)video audio:(NSString *)audio {
    if (!_mpv) {
        _mpv=mpv_create(); if (!_mpv) return NO;
        [self setOption:"config" value:"no"]; [self setOption:"vo" value:"libmpv"];
        [self setOption:"keep-open" value:"yes"]; [self setOption:"video-sync" value:"audio"];
        [self setOption:"hwdec" value:"auto-safe"]; [self setOption:"audio-display" value:"no"];
        // Match the proven stable SingWS mpv backend. In-process property/GPU
        // activity can occasionally hold an mpv core lock for over 400ms, so
        // the former 400ms audio buffer had no safety margin at all.
        [self setOption:"cache" value:"yes"];
        [self setOption:"demuxer-max-bytes" value:"256MiB"];
        [self setOption:"audio-buffer" value:"1.0"];
        [self setOption:"demuxer-readahead-secs" value:"10"];
        [self setOption:"input-default-bindings" value:"no"];
        [self setOption:"pause" value:"yes"];
        mpv_request_log_messages(_mpv,"warn");
        mpv_set_wakeup_callback(_mpv,eventWake,(__bridge void *)self);
        int r=mpv_initialize(_mpv); if (r<0) return NO;
        bridgeLog("[bridge] playback buffering audio=1.0s readahead=10s cache=256MiB");
        [_master makeCurrentContext];
        mpv_opengl_init_params init={.get_proc_address=getProc,.get_proc_address_ctx=nullptr};
        mpv_render_param params[]={{MPV_RENDER_PARAM_API_TYPE,(void *)MPV_RENDER_API_TYPE_OPENGL},
            {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,&init},{MPV_RENDER_PARAM_INVALID,nullptr}};
        r=mpv_render_context_create(&_render,_mpv,params); if (r<0) return NO;
        mpv_render_context_set_update_callback(_render,renderWake,(__bridge void *)self);
    } else {
        // mpv's loadfile command returns before the previous file has stopped.
        // audio-files and several playback properties are file-local, so
        // setting them while that teardown is still pending can make adjacent
        // loads inherit different state. Complete the cheap stop command
        // before configuring the replacement file.
        const char *stop[]={"stop",nullptr};
        int stopResult=mpv_command(_mpv,stop);
        if(stopResult<0)
            bridgeLog("[bridge] pre-load stop failed: %s",mpv_error_string(stopResult));
    }

    // Reuse the one initialized mpv core and its two child views. Clear the
    // previous texture before replacing media so a stopped song cannot flash
    // during the next decoder's startup.
    _loading=YES;
    _playWhenLoaded=NO;
    int paused=1;
    mpv_set_property(_mpv,"pause",MPV_FORMAT_FLAG,&paused);
    _hasFrame=NO;
    _isCdg=[[video.pathExtension lowercaseString] isEqualToString:@"cdg"];
    [_outputView setNeedsDisplay:YES]; [_previewView setNeedsDisplay:YES];
    // The IINA-bundled FFmpeg gives a few valid CDG files a very low probe
    // score. Lower the acceptance threshold for CDG loads, while retaining
    // automatic format detection for both the CDG and its external MP3. This
    // preserves the audio clock and resets the normal threshold for MP4/audio.
    int probeResult=mpv_set_property_string(_mpv,"demuxer-lavf-probescore",_isCdg?"1":"26");
    if(probeResult<0)
        bridgeLog("[bridge] demuxer probe threshold failed: %s",mpv_error_string(probeResult));
    int r=0;
    if (audio.length) {
        r=mpv_set_property_string(_mpv,"audio-files",audio.fileSystemRepresentation);
    } else {
        mpv_node_list emptyList={.num=0,.values=nullptr,.keys=nullptr};
        mpv_node emptyNode={}; emptyNode.format=MPV_FORMAT_NODE_ARRAY;
        emptyNode.u.list=&emptyList;
        r=mpv_set_property(_mpv,"audio-files",MPV_FORMAT_NODE,&emptyNode);
    }
    if (r<0) {
        bridgeLog("[bridge] audio-files failed: %s",mpv_error_string(r));
        return NO;
    }
    const uint64_t serial=++_loadSerial;
    bridgeLog("[bridge] load queued serial=%llu video=%s audio=%s",
            (unsigned long long)serial,video.fileSystemRepresentation,
            audio.length?audio.fileSystemRepresentation:"(internal)");
    const char *cmd[]={"loadfile",video.fileSystemRepresentation,"replace",nullptr};
    int loadResult=mpv_command_async(_mpv,serial,cmd);
    if(loadResult<0)_loading=NO;
    return loadResult>=0;
}

- (void)setPaused:(BOOL)paused {
    if(!_mpv)return;
    _playWhenLoaded=!paused;
    if(_loading && !paused)return;
    int flag=paused?1:0;
    mpv_set_property(_mpv,"pause",MPV_FORMAT_FLAG,&flag);
}
- (void)stopPlayback {
    if(!_mpv)return;
    _loading=NO; _playWhenLoaded=NO;
    const char *cmd[]={"stop",nullptr}; mpv_command_async(_mpv,3,cmd);
    _hasFrame=NO; [_outputView setNeedsDisplay:YES]; [_previewView setNeedsDisplay:YES];
}
- (void)seekMilliseconds:(int64_t)milliseconds {
    if(!_mpv)return; char value[64]; snprintf(value,sizeof(value),"%.3f",MAX(0LL,milliseconds)/1000.0);
    const char *cmd[]={"seek",value,"absolute+exact",nullptr}; mpv_command_async(_mpv,4,cmd);
}
- (double)doubleProperty:(const char *)name fallback:(double)fallback {
    if(!_mpv)return fallback; double value=fallback;
    return mpv_get_property(_mpv,name,MPV_FORMAT_DOUBLE,&value)>=0?value:fallback;
}
- (BOOL)flagProperty:(const char *)name fallback:(BOOL)fallback {
    if(!_mpv)return fallback; int value=fallback?1:0;
    return mpv_get_property(_mpv,name,MPV_FORMAT_FLAG,&value)>=0?(value!=0):fallback;
}
- (int64_t)positionMilliseconds {
    // mpv can hold its core lock for several hundred milliseconds while an
    // external MP3 and CDG are opening. The Qt timers poll these getters on
    // the GUI thread; report "not ready" until FILE_LOADED instead of making
    // the whole interface wait on that lock.
    if(_loading)return 0;
    // CDG packets update sparsely, so the general time-pos can appear frozen
    // between graphics commands. External MP3 audio is the authoritative,
    // continuously advancing playback clock for MP3+G.
    double audioPts = [self doubleProperty:"audio-pts" fallback:-1.0];
    double seconds = audioPts >= 0.0
        ? audioPts
        : [self doubleProperty:"time-pos" fallback:0.0];
    return (int64_t)llround(seconds * 1000.0);
}
- (int64_t)durationMilliseconds {
    if(_loading)return 0;
    return (int64_t)llround([self doubleProperty:"duration" fallback:0]*1000.0);
}
- (BOOL)isPlaying {
    if(_loading)return NO;
    return _mpv && ![self flagProperty:"pause" fallback:YES] && ![self flagProperty:"idle-active" fallback:YES];
}
- (BOOL)atEnd {
    if(_loading)return NO;
    return _mpv && [self flagProperty:"eof-reached" fallback:NO];
}
- (BOOL)visualReady { return _hasFrame; }
- (void)setVolumePercent:(double)value {
    if(!_mpv)return; double volume=MAX(0.0,MIN(100.0,value));
    mpv_set_property(_mpv,"volume",MPV_FORMAT_DOUBLE,&volume);
}
- (void)setAudioDeviceName:(NSString *)name {
    if(!_mpv)return; mpv_set_property_string(_mpv,"audio-device",(name.length?name:@"auto").UTF8String);
}
- (void)setTempoPercent:(int)percent {
    _desiredTempoPercent=MAX(25,MIN(400,percent));
    if(!_mpv||_loading)return;
    double speed=_desiredTempoPercent/100.0;
    mpv_set_property(_mpv,"speed",MPV_FORMAT_DOUBLE,&speed);
}
// Key and the SingWS DSP chain (normalize -> EQ -> master bus) share mpv's
// single "af" property, so neither may write it directly: an earlier version
// had setSemitones: overwrite "af" outright, which silently wiped the EQ and
// master bus every time the host changed key. Both inputs are stored and this
// composes them, matching mpv_audio_filters.build_af_chain's order (key first,
// then the DSP stages).
- (void)applyAudioFilters {
    if(!_mpv||_loading)return;
    std::string chain;
    if(_desiredSemitones!=0){
        char pitch[64];
        snprintf(pitch,sizeof(pitch),"rubberband=pitch-scale=%.6f",
                 pow(2.0,_desiredSemitones/12.0));
        chain=pitch;
    }
    if(!_dspChain.empty()){
        if(!chain.empty())chain+=",";
        chain+=_dspChain;
    }
    int r=mpv_set_property_string(_mpv,"af",chain.c_str());
    if(r<0)bridgeLog("[bridge] af chain rejected (%s): %s",
                   mpv_error_string(r),chain.c_str());
    else bridgeLog("[bridge] af key=%d dsp=%zu chars -> %s",
                 _desiredSemitones,_dspChain.size(),
                 chain.empty()?"(passthrough)":chain.c_str());
}
- (void)setSemitones:(int)semitones {
    _desiredSemitones=semitones;
    [self applyAudioFilters];
}
- (void)setDspChain:(const char *)chain {
    _dspChain=chain?chain:"";
    [self applyAudioFilters];
}
// SingWS's CDG calibration is a VISUAL LEAD: display_position =
// audible_position + offset, so a positive offset shows lyrics earlier. mpv's
// "audio-delay" delays audio relative to video, which moves the picture ahead
// by the same amount -- same sign, direct mapping, no conversion needed.
//
// This is the single-core equivalent of the follower design's dead
// set_video_offset_ms(): there, mpv chased SingWS's audio clock and had
// nowhere to apply a visual offset, so CDG ran ~750ms out with the Settings
// slider doing nothing.
- (void)setAudioDelaySeconds:(double)seconds {
    _desiredAudioDelay=std::max(-3.0,std::min(3.0,seconds));
    if(!_mpv||_loading)return;
    double value=_desiredAudioDelay;
    int r=mpv_set_property(_mpv,"audio-delay",MPV_FORMAT_DOUBLE,&value);
    if(r<0)bridgeLog("[bridge] audio-delay rejected: %s",mpv_error_string(r));
    else bridgeLog("[bridge] audio-delay=%.3fs (CDG visual lead)",value);
}
- (void)setCdgSidefill:(BOOL)enabled {
    _cdgSidefill=enabled;
    [_outputView setNeedsDisplay:YES];
}

- (void)beginWindowTransition:(int)durationMs {
    if(_stopping.load())return;
    const uint64_t serial=++_transitionSerial;
    _outputTransitioning=true;
    const int settleMs=MAX(100,durationMs)+80;
    bridgeLog("[bridge] output transition hold serial=%llu duration=%dms",
            (unsigned long long)serial,settleMs);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)settleMs*NSEC_PER_MSEC),
                   dispatch_get_main_queue(),^{
        if(self->_stopping.load() || self->_transitionSerial.load()!=serial)return;
        self->_outputTransitioning=false;
        // CDG may have no new graphics packet after the resize. Explicitly
        // present the latest shared texture at settled geometry rather than
        // waiting for another mpv render callback.
        [self presentView:self->_outputView];
        [self->_outputView setNeedsDisplay:YES];
        bridgeLog("[bridge] output transition released serial=%llu",
                (unsigned long long)serial);
    });
}

- (void)scheduleRender {
    if (_stopping.load()) return; bool expected=false;
    if (!_renderQueued.compare_exchange_strong(expected,true)) return;
    dispatch_async(dispatch_get_main_queue(),^{ self->_renderQueued=false; if(!self->_stopping.load())[self renderFrame]; });
}
- (void)scheduleEvents {
    if (_stopping.load()) return; bool expected=false;
    if (!_eventQueued.compare_exchange_strong(expected,true)) return;
    dispatch_async(dispatch_get_main_queue(),^{ self->_eventQueued=false; if(!self->_stopping.load())[self drainEvents]; });
}
- (void)drainEvents {
    while (_mpv) { mpv_event *e=mpv_wait_event(_mpv,0); if(!e||e->event_id==MPV_EVENT_NONE)break;
        if(e->event_id==MPV_EVENT_LOG_MESSAGE){mpv_event_log_message*m=(mpv_event_log_message*)e->data;
            bridgeLog("[mpv/%s] %s",m->level,m->text);}
        else if(e->event_id==MPV_EVENT_FILE_LOADED){
            _loading=NO;
            // Apply file-local tuning only after the replacement file owns the
            // playback state, then honor the Play request Python made while
            // loadfile was pending.
            int tempo=_desiredTempoPercent, semitones=_desiredSemitones;
            [self setTempoPercent:tempo];
            // Re-composes key AND the DSP chain. Without the DSP half here the
            // EQ/master bus would survive only until the next song.
            [self setSemitones:semitones];
            // loadfile resets audio-delay, so the CDG calibration has to be
            // re-asserted or it would apply only to the first song.
            [self setAudioDelaySeconds:_desiredAudioDelay];
            BOOL shouldPlay=_playWhenLoaded;
            int paused=shouldPlay?0:1;
            mpv_set_property(_mpv,"pause",MPV_FORMAT_FLAG,&paused);
            bridgeLog("[bridge] media loaded serial=%llu play=%d tempo=%d key=%d",
                    (unsigned long long)_loadSerial,shouldPlay?1:0,tempo,semitones);
        }else if(e->event_id==MPV_EVENT_END_FILE && _loading){
            mpv_event_end_file *end=(mpv_event_end_file *)e->data;
            if(end && end->reason==MPV_END_FILE_REASON_ERROR){
                _loading=NO;
                bridgeLog("[bridge] load ended with error: %s",mpv_error_string(end->error));
            }
        } }
}
- (void)renderFrame {
    if(!_render)return; uint64_t flags=mpv_render_context_update(_render);
    if(flags&MPV_RENDER_UPDATE_FRAME){ [_master makeCurrentContext]; CGLLockContext(_master.CGLContextObj);
        glBindFramebuffer(GL_FRAMEBUFFER,_fbo); glViewport(0,0,_width,_height);
        mpv_opengl_fbo target={(int)_fbo,_width,_height,GL_RGBA8}; int flip=1;
        mpv_render_param p[]={{MPV_RENDER_PARAM_OPENGL_FBO,&target},{MPV_RENDER_PARAM_FLIP_Y,&flip},{MPV_RENDER_PARAM_INVALID,nullptr}};
        // glFlush, not glFinish: this runs on the Qt GUI thread (see
        // queueRender), so blocking until the GPU drains stalls the whole UI
        // once per frame and shows up as choppy playback on integrated GPUs.
        // The following presentView drawing is ordered against this work by the
        // shared context, and each flushBuffer syncs before it swaps.
        mpv_render_context_render(_render,p); glBindFramebuffer(GL_FRAMEBUFFER,0); glFlush();
        CGLUnlockContext(_master.CGLContextObj); _hasFrame=YES; }
    [self presentView:_outputView]; [self presentView:_previewView];
}
- (void)presentView:(BridgeVideoView *)view {
    if(!view)return;  // nil belongs to neither view; labelling it would mislead
    // Why a present was skipped is the whole question when one window goes
    // blank while the other keeps drawing, so name the reason. Rate-limited to
    // one line per view per reason-change: this runs at frame rate.
    const BOOL isOutput = (view == _outputView);
    int state = 0;
    if(!view.openGLContext) state = 1;
    else if(isOutput && _outputTransitioning.load()) state = 2;
    else if(view.isHiddenOrHasHiddenAncestor) state = 3;
    else if(!view.window) state = 4;
    else if(!view.window.isVisible) state = 5;
    // Not a skip -- the view is still cleared to black below, exactly as
    // before. Reported because "drawing, but there is no frame to draw" and
    // "not drawing at all" look identical on screen and have different causes.
    else if(!_hasFrame) state = 6;
    int &last = isOutput ? _lastOutputSkip : _lastPreviewSkip;
    if(state != last){
        last = state;
        static const char *why[]={"presenting","no-gl-context","window-transition",
                                  "view-hidden","no-window","window-not-visible",
                                  "no-frame (black)"};
        bridgeLog("[bridge] %s view %s", isOutput?"output":"preview", why[state]);
    }
    if(state && state != 6) return;
    NSOpenGLContext*ctx=view.openGLContext;
    // Drawing a view nobody can see costs a full clear+draw+flushBuffer per
    // frame on the GUI thread. During a show the preview is often closed, so
    // this halves the per-frame cost outright. Safe to skip: BridgeVideoView's
    // drawRect: calls back into presentView, so AppKit repaints from the
    // retained shared texture as soon as the view is on screen again.
    [ctx makeCurrentContext]; [ctx update]; CGLLockContext(ctx.CGLContextObj);
    NSSize s=[view convertSizeToBacking:view.bounds.size]; int w=MAX(1,(int)s.width),h=MAX(1,(int)s.height);
    glBindFramebuffer(GL_FRAMEBUFFER,0); glViewport(0,0,w,h); glClearColor(0,0,0,1); glClear(GL_COLOR_BUFFER_BIT);
    if(_hasFrame){ double sa=(double)_width/_height,va=(double)w/h; GLfloat sx=1,sy=1;
        if(va>sa)sx=sa/va; else sy=va/sa;
        GLfloat uvx=1,uvy=1,uox=0,uoy=0;
        if(view==_previewView && _isCdg){
            // libmpv presents CDG as 4:3 inside the shared 16:9 texture, so the
            // picture lives between x=.125 and x=.875. Discard those generated
            // pillar bars, then letterbox what is left against the host's real
            // aspect. This used to draw the cropped region edge-to-edge on the
            // assumption that the preview host was a fixed 300x216 (25:18)
            // panel; it is a freely resizable window whose sole child fills it,
            // so the picture was stretched by whatever the mismatch happened to
            // be -- 4% at the default size, 33% once resized to 16:9.
            uvx=0.75f; uox=0.125f;
            const double ca=4.0/3.0;
            if(va>ca)sx=(GLfloat)(ca/va); else sy=(GLfloat)(va/ca);
        }
        int sidefill=(view==_outputView && _isCdg && _cdgSidefill)?1:0;
        glUseProgram(_program); GLuint*vao=(view==_outputView)?&_outVao:&_prevVao;
        if(!*vao)glGenVertexArrays(1,vao); glBindVertexArray(*vao);
        glUniform2f(_scaleUniform,sx,sy); glUniform2f(_uvScaleUniform,uvx,uvy);
        glUniform2f(_uvOffsetUniform,uox,uoy); glUniform1i(_sidefillUniform,sidefill);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D,_texture); glUniform1i(_textureUniform,0);
        glDrawArrays(GL_TRIANGLE_STRIP,0,4); glBindTexture(GL_TEXTURE_2D,0); glBindVertexArray(0); glUseProgram(0); }
    [ctx flushBuffer]; CGLUnlockContext(ctx.CGLContextObj);
}
- (void)shutdown {
    if(_stopping.exchange(true))return;
    if(_render)mpv_render_context_set_update_callback(_render,nullptr,nullptr);
    if(_mpv)mpv_set_wakeup_callback(_mpv,nullptr,nullptr);
    if(_render){[_master makeCurrentContext];mpv_render_context_free(_render);_render=nullptr;}
    if(_mpv){mpv_terminate_destroy(_mpv);_mpv=nullptr;}
    [_outputView removeFromSuperview]; [_previewView removeFromSuperview];
    bridgeLog("[bridge] clean shutdown");
}
@end

extern "C" {
void singws_bridge_set_log_callback(SingWSBridgeLogFn cb) { g_bridgeLog.store(cb); }

void *singws_bridge_create(uintptr_t outputView, uintptr_t previewView,
                           const char *videoPath, const char *audioPath) {
    @autoreleasepool {
        NSView *out=(__bridge NSView *)(void *)outputView;
        NSView *prev=(__bridge NSView *)(void *)previewView;
        BridgeRenderer *renderer=[[BridgeRenderer alloc] initWithOutput:out preview:prev];
        if(!renderer)return nullptr;
        NSString *video=[NSString stringWithUTF8String:videoPath?:""];
        NSString *audio=audioPath&&*audioPath?[NSString stringWithUTF8String:audioPath]:nil;
        if(![renderer loadVideo:video audio:audio]){[renderer shutdown];return nullptr;}
        return (__bridge_retained void *)renderer;
    }
}
int singws_bridge_load(void *handle, const char *videoPath, const char *audioPath) {
    if (!handle || !videoPath || !*videoPath) return 0;
    @autoreleasepool {
        BridgeRenderer *renderer=(__bridge BridgeRenderer *)handle;
        NSString *video=[NSString stringWithUTF8String:videoPath];
        NSString *audio=audioPath&&*audioPath?[NSString stringWithUTF8String:audioPath]:nil;
        return [renderer loadVideo:video audio:audio] ? 1 : 0;
    }
}
void singws_bridge_destroy(void *handle) {
    if(!handle)return; @autoreleasepool {
        BridgeRenderer *renderer=(__bridge_transfer BridgeRenderer *)handle; [renderer shutdown];
    }
}
void singws_bridge_play(void *h) { if(h)[(__bridge BridgeRenderer *)h setPaused:NO]; }
void singws_bridge_pause(void *h) { if(h)[(__bridge BridgeRenderer *)h setPaused:YES]; }
void singws_bridge_stop(void *h) { if(h)[(__bridge BridgeRenderer *)h stopPlayback]; }
void singws_bridge_seek(void *h, int64_t ms) { if(h)[(__bridge BridgeRenderer *)h seekMilliseconds:ms]; }
int64_t singws_bridge_position(void *h) { return h?[(__bridge BridgeRenderer *)h positionMilliseconds]:0; }
int64_t singws_bridge_duration(void *h) { return h?[(__bridge BridgeRenderer *)h durationMilliseconds]:0; }
int singws_bridge_is_playing(void *h) { return h&&[(__bridge BridgeRenderer *)h isPlaying]; }
int singws_bridge_at_end(void *h) { return h&&[(__bridge BridgeRenderer *)h atEnd]; }
int singws_bridge_visual_ready(void *h) { return h&&[(__bridge BridgeRenderer *)h visualReady]; }
void singws_bridge_set_volume(void *h,double v){if(h)[(__bridge BridgeRenderer *)h setVolumePercent:v];}
void singws_bridge_set_device(void *h,const char*n){if(h)[(__bridge BridgeRenderer *)h setAudioDeviceName:n?[NSString stringWithUTF8String:n]:@"auto"];}
void singws_bridge_set_tempo(void *h,int p){if(h)[(__bridge BridgeRenderer *)h setTempoPercent:p];}
void singws_bridge_set_key(void *h,int n){if(h)[(__bridge BridgeRenderer *)h setSemitones:n];}
void singws_bridge_set_dsp_chain(void *h,const char*c){if(h)[(__bridge BridgeRenderer *)h setDspChain:c];}
void singws_bridge_set_audio_delay(void *h,double s){if(h)[(__bridge BridgeRenderer *)h setAudioDelaySeconds:s];}
void singws_bridge_set_sidefill(void *h,int e){if(h)[(__bridge BridgeRenderer *)h setCdgSidefill:e!=0];}
void singws_bridge_begin_transition(void *h,int ms){if(h)[(__bridge BridgeRenderer *)h beginWindowTransition:ms];}
int singws_bridge_scan_silence(const char *path,double noiseDb,double leadMinimum,
                               double trailMinimum,double *lead,double *trail,
                               double *duration){
    @autoreleasepool {
        return scanSilence(path,noiseDb,leadMinimum,trailMinimum,lead,trail,duration);
    }
}
void singws_bridge_scanner_shutdown(void){shutdownSilenceScanner();}
}
