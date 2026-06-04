import sys
from bass_background_engine import BassBackgroundEngine

try:
    print("initial:", BassBackgroundEngine._bass_init_done)
    e1 = BassBackgroundEngine()
    print("after first:", BassBackgroundEngine._bass_init_done, BassBackgroundEngine._bass_init_refcount)
    e2 = BassBackgroundEngine()
    print("after second:", BassBackgroundEngine._bass_init_done, BassBackgroundEngine._bass_init_refcount)
    # cleanup
    try:
        e1.close()
    except Exception:
        pass
    print("after close e1:", BassBackgroundEngine._bass_init_done, BassBackgroundEngine._bass_init_refcount)
    try:
        e2.close()
    except Exception:
        pass
    print("after close e2:", BassBackgroundEngine._bass_init_done, BassBackgroundEngine._bass_init_refcount)
    print("ok")
except Exception as exc:
    print("ERROR:", exc)
    sys.exit(2)
