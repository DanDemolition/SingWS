"""Build the real host window off-screen, switch between its main views many times and report whether threads, timers,
widgets, Python objects or open files grow. Prints one JSON line. Run by test_headless_window_stability.py in its own
process because the off-screen Qt platform can crash on native surfaces (those are switched off / stubbed here).

    SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen SINGWS_QUICK_SURFACES=off python tools/headless_stability_check.py
"""
import gc
import importlib.util
import json
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SINGWS_QUICK_SURFACES", "off")
sys.path.insert(0, os.getcwd())


def main(switches=150):
    spec = importlib.util.spec_from_file_location("singws_main_stability", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["singws_main_stability"] = module
    spec.loader.exec_module(module)
    import psutil
    from PyQt6.QtCore import QThread, QTimer
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    module.force_dark_palette(app)
    module.configure_app_font(app, point_bump=5)
    # Native macOS surface re-stacking crashes the off-screen platform; it has nothing to do with what is measured here.
    for name in ("_reassert_show_window_surface",):
        if hasattr(module.KaraokeApp, name):
            setattr(module.KaraokeApp, name, lambda *a, **k: None)
    win = module.KaraokeApp()
    win.show()
    for _ in range(40):
        app.processEvents(); time.sleep(0.02)
    proc = psutil.Process()

    def snap():
        gc.collect()
        timers = win.findChildren(QTimer)
        return {"py_threads": threading.active_count(), "qtimers": len(timers), "active_timers": sum(t.isActive() for t in timers),
                "fast_timers": sum(1 for t in timers if t.isActive() and t.interval() <= 250),
                "qthreads_running": sum(1 for t in win.findChildren(QThread) if t.isRunning()),
                "widgets": len(win.findChildren(QWidget)), "py_objects": len(gc.get_objects()), "fds": proc.num_fds(),
                "rss_mb": round(proc.memory_info().rss / 1e6, 1)}

    modes = ["main", "bg", "history", "chat", "waiting"]
    for i in range(10):                                  # first visits create the lazily built views
        win._set_left_workspace_view(modes[i % 5]); app.processEvents()
    for _ in range(30):
        app.processEvents(); time.sleep(0.01)
    before = snap()
    for i in range(switches):
        win._set_left_workspace_view(modes[i % 5])
        if i % 2: app.processEvents()
    for _ in range(30):
        app.processEvents(); time.sleep(0.01)
    after = snap()
    print(json.dumps({"before": before, "after": after, "switches": switches}))


if __name__ == "__main__":
    main()
