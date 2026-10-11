"""pytest session setup.

Records the REAL PyQt `QTimer.singleShot` before any test module can replace it. Some older tests (test_bgm_master.py) overwrite the shared
class attribute with a recorder and never restore it, and a few later tests (test_karafun_lifecycle.py) only pass because real timers never fire.
Tests that genuinely need a working timer take the real one from here for their own duration (see test_bgm_stop_fade.py)."""
from PyQt6.QtCore import QTimer

REAL_SINGLE_SHOT = QTimer.singleShot
