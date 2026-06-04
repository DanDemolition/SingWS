"""PySide6/QML bridge scaffolding for the next SingWS UI.

This module is intentionally separate from the current PyQt6 widget app.  It
defines Python-owned models that QML can bind to, so the QML layer stays a view
over application state instead of hardcoding karaoke data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    Property,
    Qt,
    Signal,
    Slot,
)


@dataclass
class SongRow:
    title: str = ""
    artist: str = ""
    brand: str = ""
    duration: str = ""
    file_type: str = ""
    path: str = ""


class SongLibraryModel(QAbstractListModel):
    TitleRole = Qt.ItemDataRole.UserRole + 1
    ArtistRole = Qt.ItemDataRole.UserRole + 2
    BrandRole = Qt.ItemDataRole.UserRole + 3
    DurationRole = Qt.ItemDataRole.UserRole + 4
    PathRole = Qt.ItemDataRole.UserRole + 5
    FileTypeRole = Qt.ItemDataRole.UserRole + 6

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._rows: list[SongRow] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.TitleRole:
            return row.title
        if role == self.ArtistRole:
            return row.artist
        if role == self.BrandRole:
            return row.brand
        if role == self.DurationRole:
            return row.duration
        if role == self.PathRole:
            return row.path
        if role == self.FileTypeRole:
            return row.file_type
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            self.TitleRole: QByteArray(b"title"),
            self.ArtistRole: QByteArray(b"artist"),
            self.BrandRole: QByteArray(b"brand"),
            self.DurationRole: QByteArray(b"duration"),
            self.PathRole: QByteArray(b"path"),
            self.FileTypeRole: QByteArray(b"fileType"),
        }

    def set_tracks(self, tracks: list[dict[str, Any]]) -> None:
        rows: list[SongRow] = []
        for track in tracks:
            title = str(track.get("title") or track.get("display") or "")
            artist = str(track.get("artist") or "")
            brand = str(track.get("discid") or track.get("disc_id") or track.get("brand") or "")
            duration = str(track.get("duration_label") or track.get("duration") or "")
            path = str(track.get("path") or "")
            ext = os.path.splitext(path)[1].lstrip(".").upper() if path else ""
            rows.append(SongRow(title=title, artist=artist, brand=brand, duration=duration, file_type=ext, path=path))

        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


@dataclass
class BgmRow:
    title: str = ""
    file_type: str = ""
    is_current: bool = False
    path: str = ""


class BgmModel(QAbstractListModel):
    TitleRole = Qt.ItemDataRole.UserRole + 1
    FileTypeRole = Qt.ItemDataRole.UserRole + 2
    CurrentRole = Qt.ItemDataRole.UserRole + 3
    PathRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._rows: list[BgmRow] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.TitleRole:
            return row.title
        if role == self.FileTypeRole:
            return row.file_type
        if role == self.CurrentRole:
            return row.is_current
        if role == self.PathRole:
            return row.path
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            self.TitleRole: QByteArray(b"title"),
            self.FileTypeRole: QByteArray(b"fileType"),
            self.CurrentRole: QByteArray(b"isCurrent"),
            self.PathRole: QByteArray(b"path"),
        }

    def set_playlist(self, paths: list[str], current_index: int = -1) -> None:
        rows: list[BgmRow] = []
        for i, raw_path in enumerate(paths):
            path = str(raw_path or "")
            if not path:
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            ext = os.path.splitext(path)[1].lstrip(".").upper()
            rows.append(BgmRow(title=stem, file_type=ext, is_current=(i == current_index), path=path))

        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


@dataclass
class RotationRow:
    number: int = 0
    singer: str = ""
    song: str = ""
    eta: str = ""
    status: str = ""
    is_current: bool = False


class RotationModel(QAbstractListModel):
    NumberRole = Qt.ItemDataRole.UserRole + 1
    SingerRole = Qt.ItemDataRole.UserRole + 2
    SongRole = Qt.ItemDataRole.UserRole + 3
    EtaRole = Qt.ItemDataRole.UserRole + 4
    StatusRole = Qt.ItemDataRole.UserRole + 5
    CurrentRole = Qt.ItemDataRole.UserRole + 6

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._rows: list[RotationRow] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.NumberRole:
            return row.number
        if role == self.SingerRole:
            return row.singer
        if role == self.SongRole:
            return row.song
        if role == self.EtaRole:
            return row.eta
        if role == self.StatusRole:
            return row.status
        if role == self.CurrentRole:
            return row.is_current
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            self.NumberRole: QByteArray(b"number"),
            self.SingerRole: QByteArray(b"singer"),
            self.SongRole: QByteArray(b"song"),
            self.EtaRole: QByteArray(b"eta"),
            self.StatusRole: QByteArray(b"status"),
            self.CurrentRole: QByteArray(b"isCurrent"),
        }

    def set_rotation(self, queue: list[dict[str, Any]]) -> None:
        rows: list[RotationRow] = []
        number = 1
        for singer in queue:
            if singer.get("skipped", False):
                continue
            songs = singer.get("songs") or []
            active_song = next(
                (song for song in songs if not (isinstance(song, dict) and song.get("skipped", False))),
                None,
            )
            song_title = ""
            if isinstance(active_song, dict):
                song_title = str(active_song.get("display_name") or active_song.get("title") or "")
                status = "DUET" if str(active_song.get("duet_display") or "").strip() else ""
            elif active_song is not None:
                song_title = str(active_song)
                status = ""
            else:
                status = ""
            rows.append(
                RotationRow(
                    number=number,
                    singer=str(singer.get("name") or ""),
                    song=song_title,
                    eta=str(singer.get("eta") or ""),
                    status=status,
                    is_current=bool(singer.get("is_current", False)),
                )
            )
            number += 1

        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class TransportState(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._elapsed = "00:00"
        self._remaining = "-00:00"
        self._progress = 0.0
        self._playing = False

    def _set(self, name: str, value: Any) -> None:
        private_name = f"_{name}"
        if getattr(self, private_name) == value:
            return
        setattr(self, private_name, value)
        self.changed.emit()

    @Property(str, notify=changed)
    def elapsed(self) -> str:
        return self._elapsed

    @Property(str, notify=changed)
    def remaining(self) -> str:
        return self._remaining

    @Property(float, notify=changed)
    def progress(self) -> float:
        return self._progress

    @Property(bool, notify=changed)
    def playing(self) -> bool:
        return self._playing

    def set_state(self, *, elapsed: str, remaining: str, progress: float, playing: bool) -> None:
        self._elapsed = elapsed
        self._remaining = remaining
        self._progress = max(0.0, min(1.0, float(progress)))
        self._playing = bool(playing)
        self.changed.emit()


class SingWSQmlBridge(QObject):
    """Small command and state bridge for the QML shell."""

    changed = Signal()
    searchRequested = Signal(str)
    playRequested = Signal()
    restartRequested = Signal()
    stopRequested = Signal()
    addFileRequested = Signal()
    showRotationRequested = Signal()
    showKaraokeRequested = Signal()
    networkRequested = Signal()
    settingsRequested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.song_library_model = SongLibraryModel(self)
        self.rotation_model = RotationModel(self)
        self.bgm_model = BgmModel(self)
        self.transport = TransportState(self)
        self._requests_status = "Requests Closed"
        self._connection_status = "Offline"
        self._singer_count = 0
        self._mode = "Rotation"
        self._now_singer = ""
        self._now_artist = ""
        self._now_title = ""
        self._last_sung = "Nothing yet"
        self._last_singer = ""
        self._next_singer = ""
        self._bg_title = ""
        self._bg_artist = ""
        self._active_view = "show"

    @Property(QObject, constant=True)
    def songLibraryModel(self) -> QObject:
        return self.song_library_model

    @Property(QObject, constant=True)
    def rotationModel(self) -> QObject:
        return self.rotation_model

    @Property(QObject, constant=True)
    def bgmModel(self) -> QObject:
        return self.bgm_model

    @Property(QObject, constant=True)
    def transportState(self) -> QObject:
        return self.transport

    @Property(str, notify=changed)
    def requestsStatus(self) -> str:
        return self._requests_status

    @Property(str, notify=changed)
    def connectionStatus(self) -> str:
        return self._connection_status

    @Property(int, notify=changed)
    def singerCount(self) -> int:
        return self._singer_count

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Property(str, notify=changed)
    def nowSinger(self) -> str:
        return self._now_singer

    @Property(str, notify=changed)
    def nowArtist(self) -> str:
        return self._now_artist

    @Property(str, notify=changed)
    def nowTitle(self) -> str:
        return self._now_title

    @Property(str, notify=changed)
    def lastSung(self) -> str:
        return self._last_sung

    @Property(str, notify=changed)
    def lastSinger(self) -> str:
        return self._last_singer

    @Property(str, notify=changed)
    def nextSinger(self) -> str:
        return self._next_singer

    @Property(str, notify=changed)
    def bgTitle(self) -> str:
        return self._bg_title

    @Property(str, notify=changed)
    def bgArtist(self) -> str:
        return self._bg_artist

    @Property(str, notify=changed)
    def activeView(self) -> str:
        return self._active_view

    @Slot(str)
    def search(self, text: str) -> None:
        self.searchRequested.emit(text)

    @Slot(str)
    def setActiveView(self, view: str) -> None:
        view = str(view or "show")
        if self._active_view == view:
            return
        self._active_view = view
        self.changed.emit()

    @Slot()
    def play(self) -> None:
        self.playRequested.emit()

    @Slot()
    def restart(self) -> None:
        self.restartRequested.emit()

    @Slot()
    def stop(self) -> None:
        self.stopRequested.emit()

    @Slot()
    def addFile(self) -> None:
        self.addFileRequested.emit()

    @Slot()
    def showRotation(self) -> None:
        self.showRotationRequested.emit()

    @Slot()
    def showKaraoke(self) -> None:
        self.showKaraokeRequested.emit()

    @Slot()
    def openNetwork(self) -> None:
        self.networkRequested.emit()

    @Slot()
    def openSettings(self) -> None:
        self.settingsRequested.emit()

    def refresh_from_app(self, app: Any) -> None:
        """Pull a snapshot from the existing app without owning its logic."""
        self.song_library_model.set_tracks(list(getattr(app, "tracks", []) or []))
        self.rotation_model.set_rotation(list(getattr(app, "queue", []) or []))
        bg_player = getattr(app, "bg_player", None)
        if bg_player is not None:
            self.bgm_model.set_playlist(
                list(getattr(bg_player, "playlist", []) or []),
                int(getattr(bg_player, "current_index", -1) or -1),
            )
        else:
            self.bgm_model.set_playlist([], -1)
        self._singer_count = len([s for s in getattr(app, "queue", []) or [] if not s.get("skipped", False)])
        self._mode = "Rotation" if getattr(app, "_is_rotation_mode", lambda: False)() else "Classic"
        self._now_singer = str(getattr(app, "_current_karaoke_singer_display", "") or "")
        self._now_artist = str(getattr(app, "_last_sung_artist", "") or "")
        self._now_title = str(getattr(app, "_last_sung_title", "") or "")
        self._last_sung = str(getattr(app, "_last_sung_title", "") or "Nothing yet")
        self._last_singer = str(getattr(app, "_last_sung_singer_display", "") or "")

        active_queue = [s for s in getattr(app, "queue", []) or [] if not s.get("skipped", False)]
        next_singer = ""
        if active_queue:
            current_idx = next(
                (i for i, s in enumerate(active_queue) if s.get("is_current", False)),
                -1,
            )
            if current_idx >= 0 and len(active_queue) > 1:
                nxt = active_queue[(current_idx + 1) % len(active_queue)]
                next_singer = str(nxt.get("name") or "")
            elif current_idx < 0:
                next_singer = str(active_queue[0].get("name") or "")
        self._next_singer = next_singer

        self.changed.emit()
