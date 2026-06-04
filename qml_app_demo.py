"""Demo launcher that seeds the QML shell with sample data for visual review."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from qml_backend import SingWSQmlBridge


SAMPLE_TRACKS = [
    {"title": "Don't Stop Believin'", "artist": "Journey", "discid": "SC8001", "duration": "4:11", "path": "/library/sc8001-journey.cdg"},
    {"title": "Sweet Caroline", "artist": "Neil Diamond", "discid": "SC8002", "duration": "3:23", "path": "/library/sc8002-diamond.mp3"},
    {"title": "Friends in Low Places", "artist": "Garth Brooks", "discid": "SC8003", "duration": "4:23", "path": "/library/sc8003-brooks.zip"},
    {"title": "Bohemian Rhapsody", "artist": "Queen", "discid": "SF1042", "duration": "5:55", "path": "/library/sf1042-queen.mp4"},
    {"title": "Living on a Prayer", "artist": "Bon Jovi", "discid": "SC8004", "duration": "4:09", "path": "/library/sc8004-bonjovi.cdg"},
    {"title": "I Will Survive", "artist": "Gloria Gaynor", "discid": "SC8005", "duration": "3:18", "path": "/library/sc8005-gaynor.mp3"},
    {"title": "Wonderwall", "artist": "Oasis", "discid": "MAN014", "duration": "4:18", "path": "/library/man014-oasis.zip"},
    {"title": "Like a Prayer", "artist": "Madonna", "discid": "MAN015", "duration": "5:41", "path": "/library/man015-madonna.cdg"},
    {"title": "Mr. Brightside", "artist": "The Killers", "discid": "MAN016", "duration": "3:43", "path": "/library/man016-killers.mp4"},
    {"title": "Hey Ya!", "artist": "OutKast", "discid": "MAN017", "duration": "3:55", "path": "/library/man017-outkast.mp3"},
    {"title": "Take On Me", "artist": "a-ha", "discid": "MAN018", "duration": "3:46", "path": "/library/man018-aha.cdg"},
    {"title": "Africa", "artist": "Toto", "discid": "MAN019", "duration": "4:55", "path": "/library/man019-toto.zip"},
]


class _FakeBgPlayer:
    playlist = [
        "/bgm/Smooth Operator - Sade.mp3",
        "/bgm/September - Earth, Wind & Fire.flac",
        "/bgm/Get Lucky - Daft Punk.mp3",
        "/bgm/Easy - Commodores.wav",
        "/bgm/Lovely Day - Bill Withers.mp3",
        "/bgm/Stayin Alive - Bee Gees.flac",
        "/bgm/Le Freak - Chic.mp3",
        "/bgm/Sir Duke - Stevie Wonder.mp3",
    ]
    current_index = 2


class _FakeApp:
    tracks = SAMPLE_TRACKS
    queue = [
        {"name": "Alex", "songs": [{"display_name": "Sweet Caroline"}], "eta": "now", "is_current": True},
        {"name": "Bailey", "songs": [{"display_name": "Wonderwall"}], "eta": "+4m"},
        {"name": "Casey", "songs": [{"display_name": "Mr. Brightside", "duet_display": "with Devin"}], "eta": "+9m"},
        {"name": "Devin", "songs": [{"display_name": "Africa"}], "eta": "+13m"},
        {"name": "Emerson", "songs": [{"display_name": "Hey Ya!"}], "eta": "+17m"},
    ]
    bg_player = _FakeBgPlayer()
    _current_karaoke_singer_display = "Alex"
    _last_sung_singer_display = "Riley"
    _last_sung_artist = "Neil Diamond"
    _last_sung_title = "Sweet Caroline"

    @staticmethod
    def _is_rotation_mode() -> bool:
        return True


def main() -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("SingWS QML (demo data)")
    app.setFont(QFont("Helvetica Neue", 13))

    bridge = SingWSQmlBridge()
    bridge.refresh_from_app(_FakeApp())

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("singws", bridge)
    ctx.setContextProperty("songLibraryModel", bridge.song_library_model)
    ctx.setContextProperty("rotationModel", bridge.rotation_model)
    ctx.setContextProperty("bgmModel", bridge.bgm_model)
    ctx.setContextProperty("transportState", bridge.transport)

    qml_path = Path(__file__).resolve().parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
