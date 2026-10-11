// Thin AppKit/OpenGL shim for the Rust video bridge (macOS presenter). NO logic lives here: it creates the NSOpenGLViews and contexts,
// reports what AppKit says about them, and forwards drawRect / reshape / "moved to window" to Rust. Everything else (textures, shaders,
// what to draw when, the skip reasons) is in render.rs. The classes mirror BridgeVideoView / the context setup in bridge.mm.
#import <Cocoa/Cocoa.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl3.h>
#include <dispatch/dispatch.h>
#include <stdint.h>

typedef void (*ShimViewEvent)(void *user, void *view, int kind);   // 0 draw, 1 geometry changed, 2 attached to a window

@interface ShimVideoView : NSOpenGLView
@property(nonatomic) ShimViewEvent cb;
@property(nonatomic) void *user;
@property(nonatomic) BOOL ignoresMouse;
@property(nonatomic) BOOL lockUpdate;   // render-thread mode: -update must not run concurrently with drawing on the same context
@end

@implementation ShimVideoView
- (void)drawRect:(NSRect)dirty { (void)dirty; if(self.cb)self.cb(self.user,(__bridge void *)self,0); }
- (void)update {
    // Apple's multithreaded-OpenGL rule. Only taken in render-thread mode, so the default path is unchanged.
    CGLContextObj cgl=(self.lockUpdate && self.openGLContext)?self.openGLContext.CGLContextObj:NULL;
    if(cgl)CGLLockContext(cgl);
    [super update];
    if(cgl)CGLUnlockContext(cgl);
}
- (NSView *)hitTest:(NSPoint)point { return self.ignoresMouse ? nil : [super hitTest:point]; }
- (void)reshape { [super reshape]; if(self.cb)self.cb(self.user,(__bridge void *)self,1); }
- (void)viewDidMoveToWindow {
    [super viewDidMoveToWindow];
    if(self.cb)self.cb(self.user,(__bridge void *)self,1);
    // Qt may reconnect this native child long after QWidget::showEvent and its bounded retries. AppKit is authoritative about when a
    // drawable really exists, so present at this lifecycle edge instead of guessing a delay.
    if(self.window && self.cb){
        __weak ShimVideoView *weakSelf=self;
        dispatch_async(dispatch_get_main_queue(),^{
            ShimVideoView *view=weakSelf;
            if(view.window && view.cb)view.cb(view.user,(__bridge void *)view,2);
        });
    }
}
- (void)mouseDown:(NSEvent *)event {
    // The Qt app owns a same-Space borderless presentation mode: forward a double click into Qt's host NSView so
    // VideoWindow.toggle_fullscreen() stays the one authoritative path (AppKit toggleFullScreen stranded a CDG view in another Space).
    if(event.clickCount==2 && self.superview){ [self.superview mouseDown:event]; return; }
    [super mouseDown:event];
}
@end

@interface ShimMaster : NSObject
@property(nonatomic,strong) NSOpenGLPixelFormat *format;
@property(nonatomic,strong) NSOpenGLContext *context;
@end
@implementation ShimMaster
@end

void *shim_master_create(void) {
    @autoreleasepool {
        NSOpenGLPixelFormatAttribute attrs[]={NSOpenGLPFAOpenGLProfile,NSOpenGLProfileVersion3_2Core,
            NSOpenGLPFAAccelerated,NSOpenGLPFADoubleBuffer,NSOpenGLPFAColorSize,24,NSOpenGLPFAAlphaSize,8,0};
        ShimMaster *m=[ShimMaster new];
        m.format=[[NSOpenGLPixelFormat alloc] initWithAttributes:attrs];
        if(!m.format)return NULL;
        m.context=[[NSOpenGLContext alloc] initWithFormat:m.format shareContext:nil];
        if(!m.context)return NULL;
        return (__bridge_retained void *)m;
    }
}
void shim_master_make_current(void *master) { [((__bridge ShimMaster *)master).context makeCurrentContext]; }
void shim_master_lock(void *master) { CGLLockContext(((__bridge ShimMaster *)master).context.CGLContextObj); }
void shim_master_unlock(void *master) { CGLUnlockContext(((__bridge ShimMaster *)master).context.CGLContextObj); }
void shim_master_destroy(void *master) { if(master){ ShimMaster *m=(__bridge_transfer ShimMaster *)master; (void)m; } }

void *shim_view_create(void *master, uintptr_t parent, ShimViewEvent cb, void *user) {
    @autoreleasepool {
        ShimMaster *m=(__bridge ShimMaster *)master;
        NSView *host=(__bridge NSView *)(void *)parent;
        if(!m || !host)return NULL;
        ShimVideoView *view=[[ShimVideoView alloc] initWithFrame:host.bounds pixelFormat:m.format];
        NSOpenGLContext *ctx=[[NSOpenGLContext alloc] initWithFormat:m.format shareContext:m.context];
        [view setOpenGLContext:ctx];
        view.cb=cb; view.user=user;
        view.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
        [host addSubview:view positioned:NSWindowBelow relativeTo:nil];
        return (__bridge_retained void *)view;
    }
}
void shim_view_destroy(void *view) {
    if(!view)return;
    @autoreleasepool {
        ShimVideoView *v=(__bridge_transfer ShimVideoView *)view;
        v.cb=NULL; v.user=NULL;
        [v removeFromSuperview];
    }
}
// bit 0: has a GL context, bit 1: hidden (or an ancestor is), bit 2: has a window, bit 3: window visible.
int shim_view_state(void *view, int *backing_w, int *backing_h) {
    ShimVideoView *v=(__bridge ShimVideoView *)view;
    NSSize s=[v convertSizeToBacking:v.bounds.size];
    if(backing_w)*backing_w=(int)s.width>1?(int)s.width:1;
    if(backing_h)*backing_h=(int)s.height>1?(int)s.height:1;
    int bits=0;
    if(v.openGLContext)bits|=1;
    if(v.isHiddenOrHasHiddenAncestor)bits|=2;
    if(v.window)bits|=4;
    if(v.window && v.window.isVisible)bits|=8;
    return bits;
}
void shim_view_make_current(void *view) { [((__bridge ShimVideoView *)view).openGLContext makeCurrentContext]; }
void shim_view_update(void *view) { [((__bridge ShimVideoView *)view).openGLContext update]; }
void shim_view_lock(void *view) { CGLLockContext(((__bridge ShimVideoView *)view).openGLContext.CGLContextObj); }
void shim_view_unlock(void *view) { CGLUnlockContext(((__bridge ShimVideoView *)view).openGLContext.CGLContextObj); }
void shim_view_flush(void *view) { [((__bridge ShimVideoView *)view).openGLContext flushBuffer]; }
void shim_view_set_needs_display(void *view) { if(view)[((__bridge ShimVideoView *)view) setNeedsDisplay:YES]; }

// nativeViewDidAttach: reconnect the drawable once AppKit says the view really has a window.
int shim_view_attach(void *view) {
    ShimVideoView *v=(__bridge ShimVideoView *)view;
    NSOpenGLContext *ctx=v.openGLContext;
    if(!v.window || !ctx)return 0;
    CGLLockContext(ctx.CGLContextObj);
    [ctx clearDrawable]; [ctx setView:v]; [ctx update];
    CGLUnlockContext(ctx.CGLContextObj);
    return 1;
}

// refreshViewsOutput:preview: for one view. Returns bit 0 when the view was moved to a new host.
int shim_view_refresh(void *view, uintptr_t parent) {
    ShimVideoView *v=(__bridge ShimVideoView *)view;
    NSView *host=(__bridge NSView *)(void *)parent;
    int moved=0;
    // Qt can replace a WA_NativeWindow host when its top-level is hidden: move the retained view to the current winId view.
    if(host && v.superview!=host){
        [v removeFromSuperview];
        v.frame=host.bounds;
        [host addSubview:v positioned:NSWindowBelow relativeTo:nil];
        moved=1;
    }
    if(host && v.superview==host)v.frame=host.bounds;
    NSOpenGLContext *ctx=v.openGLContext;
    if(ctx && v.window){
        // A resize does not replace the drawable; only bind when the context is not already attached to this retained view.
        CGLLockContext(ctx.CGLContextObj);
        if(ctx.view!=v){ [ctx clearDrawable]; [ctx setView:v]; }
        [ctx update];
        CGLUnlockContext(ctx.CGLContextObj);
    }
    return moved;
}

void shim_dispatch_main(void (*fn)(void *), void *ctx) { dispatch_async(dispatch_get_main_queue(),^{ fn(ctx); }); }
void shim_dispatch_main_after_ms(int ms, void (*fn)(void *), void *ctx) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)ms*NSEC_PER_MSEC),dispatch_get_main_queue(),^{ fn(ctx); });
}

// ---- rotation / spotlight underlays ------------------------------------------------------------------------------------------------
// The rotation renderer sits directly BEHIND Qt's translucent content view (a GL child inside the Qt tree is always above those pixels or
// hidden by an opaque native QWidget), at a stable low opacity, so lyrics stay visible without obscuring singer names.
void *shim_view_create_behind(void *master, uintptr_t host, ShimViewEvent cb, void *user) {
    @autoreleasepool {
        ShimMaster *m=(__bridge ShimMaster *)master;
        NSView *h=(__bridge NSView *)(void *)host;
        if(!m || !h || !h.window)return NULL;
        NSView *content=h.window.contentView;
        NSView *frame=content.superview;
        if(!content || !frame)return NULL;
        ShimVideoView *view=[[ShimVideoView alloc] initWithFrame:content.frame pixelFormat:m.format];
        NSOpenGLContext *ctx=[[NSOpenGLContext alloc] initWithFormat:m.format shareContext:m.context];
        [view setOpenGLContext:ctx];
        view.cb=cb; view.user=user;
        view.ignoresMouse=YES;
        view.alphaValue=0.04;
        view.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
        [frame addSubview:view positioned:NSWindowAbove relativeTo:content];
        return (__bridge_retained void *)view;
    }
}
void shim_view_set_ignores_mouse(void *view, int on) { ((__bridge ShimVideoView *)view).ignoresMouse=on!=0; }
void shim_view_set_lock_update(void *view, int on) { ((__bridge ShimVideoView *)view).lockUpdate=on!=0; }
void shim_view_set_alpha(void *view, double a) { ((__bridge ShimVideoView *)view).alphaValue=a; }
void shim_view_set_hidden(void *view, int hidden) { ((__bridge ShimVideoView *)view).hidden=hidden!=0; }
int shim_view_is_hidden(void *view) { return ((__bridge ShimVideoView *)view).hidden?1:0; }
void shim_view_animate_alpha(void *view, double alpha, double seconds, void (*done)(void *), void *ctx) {
    ShimVideoView *v=(__bridge ShimVideoView *)view;
    [NSAnimationContext runAnimationGroup:^(NSAnimationContext *context){
        context.duration=seconds;
        v.animator.alphaValue=alpha;
    } completionHandler:^{ if(done)done(ctx); }];
}
// Render-thread mode: what the render thread may know about a view without touching AppKit. Returns "presentable"; refreshed on the main thread.
int shim_view_probe(void *view, int *backing_w, int *backing_h) {
    ShimVideoView *v=(__bridge ShimVideoView *)view;
    NSSize s=[v convertSizeToBacking:v.bounds.size];
    if(backing_w)*backing_w=(int)s.width>1?(int)s.width:1;
    if(backing_h)*backing_h=(int)s.height>1?(int)s.height:1;
    return (v.openGLContext && !v.isHiddenOrHasHiddenAncestor && v.window && v.window.isVisible)?1:0;
}
void shim_view_update_locked(void *view) {
    ShimVideoView *v=(__bridge ShimVideoView *)view;
    NSOpenGLContext *ctx=v.openGLContext;
    if(ctx && v.window){ CGLLockContext(ctx.CGLContextObj); [ctx update]; CGLUnlockContext(ctx.CGLContextObj); }
}

// ---- the private render queue (karaoke_render_thread) ------------------------------------------------------------------------------
static char kShimQueueKey;
void *shim_queue_create(void) {
    dispatch_queue_t q=dispatch_queue_create("com.singws.mpv.render",
        dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_USER_INTERACTIVE,0));
    dispatch_queue_set_specific(q,&kShimQueueKey,(__bridge void *)q,NULL);
    return (__bridge_retained void *)q;
}
void shim_queue_async(void *queue, void (*fn)(void *), void *ctx) {
    dispatch_queue_t q=(__bridge dispatch_queue_t)queue; dispatch_async(q,^{ fn(ctx); });
}
void shim_queue_sync(void *queue, void (*fn)(void *), void *ctx) {
    dispatch_queue_t q=(__bridge dispatch_queue_t)queue; dispatch_sync(q,^{ fn(ctx); });
}
void shim_queue_after_ms(void *queue, int ms, void (*fn)(void *), void *ctx) {
    dispatch_queue_t q=(__bridge dispatch_queue_t)queue;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)ms*NSEC_PER_MSEC),q,^{ fn(ctx); });
}
int shim_queue_is_current(void *queue) { return dispatch_get_specific(&kShimQueueKey)==queue ? 1 : 0; }
void shim_queue_release(void *queue) { if(queue){ dispatch_queue_t q=(__bridge_transfer dispatch_queue_t)queue; (void)q; } }

// A repeating timer on the main run loop (common modes), used to refresh the per-view cache in render-thread mode.
void *shim_timer_start(double seconds, void (*fn)(void *), void *ctx) {
    NSTimer *t=[NSTimer timerWithTimeInterval:seconds repeats:YES block:^(NSTimer *timer){ (void)timer; fn(ctx); }];
    [[NSRunLoop mainRunLoop] addTimer:t forMode:NSRunLoopCommonModes];
    return (__bridge_retained void *)t;
}
void shim_timer_stop(void *timer) {
    if(!timer)return;
    NSTimer *t=(__bridge_transfer NSTimer *)timer;
    [t invalidate];
}
