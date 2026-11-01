"""Repro for the CDG seek dead zone (docs/design/stage-e-no-python-host-spike-2.md): headless mpv (vo=null, ao=null), CDG + external MP3, paused;
prints where an exact seek to a series of targets lands. Usage: python3 -I tools/cdg_seek_probe.py SONG.cdg SONG.mp3  (libmpv: SINGWS_LIBMPV or native/mpv_runtime/artifacts/arm64/Frameworks)."""
import ctypes, sys, time, os
L = ctypes.CDLL(os.environ.get("SINGWS_LIBMPV") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "native/mpv_runtime/artifacts/arm64/Frameworks/singws_libmpv.2.dylib"))
P=ctypes.c_void_p; S=ctypes.c_char_p
L.mpv_create.restype=P; L.mpv_set_option_string.argtypes=[P,S,S]; L.mpv_initialize.argtypes=[P]
L.mpv_command_string.argtypes=[P,S]; L.mpv_get_property_string.restype=S; L.mpv_get_property_string.argtypes=[P,S]
L.mpv_wait_event.restype=P; L.mpv_wait_event.argtypes=[P,ctypes.c_double]
import locale; locale.setlocale(locale.LC_NUMERIC,"C")
def run(cdg,mp3,target,flags,withaudio=True):
    h=L.mpv_create()
    for k,v in [("vo","null"),("ao","null"),("pause","yes"),("keep-open","yes"),("terminal","no"),("msg-level","all=no")]+([("audio-file",mp3)] if withaudio else []):
        L.mpv_set_option_string(h,k.encode(),v.encode())
    L.mpv_initialize(h); L.mpv_command_string(h,b'loadfile "%s"'%cdg.encode())
    def pump(t):
        t0=time.time()
        while time.time()-t0<t: L.mpv_wait_event(h,0.02)
    pump(1.5)
    g=lambda n:(L.mpv_get_property_string(h,n.encode()) or b"?").decode()
    before=(g("duration"),g("time-pos"),g("audio-pts"))
    L.mpv_command_string(h,b"seek %.3f %s"%(target,flags.encode())); pump(1.0)
    after=(g("time-pos"),g("audio-pts"),g("eof-reached"))
    L.mpv_command_string(h,b"set pause no"); pump(1.0)
    after2=(g("time-pos"),g("audio-pts"),g("eof-reached"))
    L.mpv_command_string(h,b"quit")
    return before,after,after2

def seq(cdg,mp3,cmds):
    h=L.mpv_create()
    for k,v in [("vo","null"),("ao","null"),("pause","yes"),("keep-open","yes"),("terminal","no"),("msg-level","all=no"),("audio-file",mp3)]:
        L.mpv_set_option_string(h,k.encode(),v.encode())
    L.mpv_initialize(h); L.mpv_command_string(h,b'loadfile "%s"'%cdg.encode())
    def pump(t):
        t0=time.time()
        while time.time()-t0<t: L.mpv_wait_event(h,0.02)
    pump(1.5); g=lambda n:(L.mpv_get_property_string(h,n.encode()) or b"?").decode(); out=[]
    for c in cmds:
        L.mpv_command_string(h,c.encode()); pump(0.8); out.append((c,g("time-pos"),g("eof-reached")))
    L.mpv_command_string(h,b"quit"); return out
cdg,mp3=sys.argv[1],sys.argv[2]
for t in (270,277,277.9,278.1,279,285):
    print(t,seq(cdg,mp3,["seek %s absolute+exact"%t])[0][1:],flush=True)
print(seq(cdg,mp3,["seek 278.0 absolute+keyframes","seek 288.6 absolute+exact"]))
print(seq(cdg,mp3,["seek 278.0 absolute+keyframes","seek 10.6 relative+exact"]))
print(seq(cdg,mp3,["seek 270 absolute+exact","seek 288.6 absolute+exact"]))
print(seq(cdg,mp3,["seek 288.6 absolute+exact","seek 250 absolute+exact","seek 288.6 absolute+exact"]))
