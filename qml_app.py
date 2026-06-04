"""Standalone PySide6/QML shell launcher for SingWS UI migration.

The current production app remains the PyQt6 widget app in 0.2.18.0.py.  This
launcher is a parallel prototype that exercises the new QML surface against
Python models and bridge signals.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from qml_backend import SingWSQmlBridge


def main() -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("SingWS QML")
    app.setFont(QFont("Helvetica Neue", 13))

    bridge = SingWSQmlBridge()
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("singws", bridge)
    context.setContextProperty("songLibraryModel", bridge.song_library_model)
    context.setContextProperty("rotationModel", bridge.rotation_model)
    context.setContextProperty("bgmModel", bridge.bgm_model)
    context.setContextProperty("transportState", bridge.transport)

    qml_path = Path(__file__).resolve().parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
