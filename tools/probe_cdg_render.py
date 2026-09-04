#!/usr/bin/env python3
"""Render-probe MP3+G archives through SingWS's native libmpv bridge.

This is a read-only diagnostic: archives are extracted into a temporary
directory, audio is muted, and several decoded frames are captured to verify
that libmpv produced non-uniform CDG pixels rather than merely signalling a
render callback.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mpv_playback_iina import MpvPlaybackPlugin  # noqa: E402


def _members(archive: Path) -> tuple[zipfile.ZipInfo, zipfile.ZipInfo]:
    with zipfile.ZipFile(archive) as source:
        files = [item for item in source.infolist() if not item.is_dir()]
        cdgs = [item for item in files if item.filename.lower().endswith(".cdg")]
        mp3s = [item for item in files if item.filename.lower().endswith(".mp3")]
    if len(cdgs) != 1 or len(mp3s) != 1:
        raise ValueError("archive must contain exactly one CDG and one MP3")
    return cdgs[0], mp3s[0]


def _extract(archive: Path, folder: Path) -> tuple[Path, Path]:
    cdg_info, mp3_info = _members(archive)
    cdg = folder / "song.cdg"
    mp3 = folder / "song.mp3"
    with zipfile.ZipFile(archive) as source:
        cdg.write_bytes(source.read(cdg_info))
        mp3.write_bytes(source.read(mp3_info))
    return cdg, mp3


def _frame_stats(image) -> dict[str, int] | None:
    if image is None or image.isNull():
        return None
    image = image.convertToFormat(image.Format.Format_RGB32)
    width, height = image.width(), image.height()
    # Sampling every third pixel is enough to distinguish a uniform/blank CDG
    # screen from titles and lyrics without making this show diagnostic heavy.
    colors = set()
    minimum, maximum = 255, 0
    for y in range(0, height, 3):
        for x in range(0, width, 3):
            pixel = image.pixel(x, y)
            red = (pixel >> 16) & 255
            green = (pixel >> 8) & 255
            blue = pixel & 255
            colors.add((red, green, blue))
            level = max(red, green, blue)
            minimum = min(minimum, level)
            maximum = max(maximum, level)
    return {
        "width": width,
        "height": height,
        "colors": len(colors),
        "range": maximum - minimum,
    }


def _wait(app: QApplication, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def probe(app: QApplication, plugin: MpvPlaybackPlugin, output: QWidget,
          archive: Path, speed: float) -> dict:
    result = {"archive": str(archive), "frames": [], "ok": False}
    with tempfile.TemporaryDirectory(prefix="singws_cdg_probe_") as raw_folder:
        cdg, mp3 = _extract(archive, Path(raw_folder))
        if not plugin.loadSingWSMedia(cdg, mp3, autoplay=True):
            result["error"] = plugin.errorString() or "native load failed"
            return result
        plugin.setVolume(0.0)
        if speed != 1.0:
            plugin.setTempoRatio(speed)
        if not _wait(app, plugin.visualsReady, 5.0):
            result["error"] = "no native visual frame within 5 seconds"
            return result
        duration = plugin.durationMs()
        result["duration_ms"] = duration
        positions = [2000, 5000, 10000, min(20000, max(1000, duration - 1000))]
        for position in positions:
            _wait(app, lambda: plugin.positionMs() >= position, 8.0)
            raw_stats = _frame_stats(plugin.grabFrame())
            visible = app.primaryScreen().grabWindow(int(output.winId())).toImage()
            visible_stats = _frame_stats(visible)
            result["frames"].append({
                "position_ms": position,
                "raw_stats": raw_stats,
                "visible_stats": visible_stats,
            })
        result["ok"] = any(
            frame["visible_stats"] is not None
            and frame["visible_stats"]["colors"] > 1
            and frame["visible_stats"]["range"] > 0
            for frame in result["frames"]
        )
        plugin.stopMedia()
        _wait(app, lambda: False, 0.1)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="*", type=Path)
    parser.add_argument("--show-log", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    archives = list(args.archives)
    if args.show_log:
        if not args.catalog:
            parser.error("--show-log requires --catalog")
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        by_name = {Path(item["path"]).name.casefold(): Path(item["path"])
                   for item in catalog if item.get("path")}
        pattern = re.compile(r"\[PLAYNEXT\] confirmed .* song=(.+)$")
        for line in args.show_log.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.search(line)
            if not match:
                continue
            remote_path = Path(ast.literal_eval(match.group(1)))
            local_path = by_name.get(remote_path.name.casefold())
            if local_path and local_path not in archives:
                archives.append(local_path)
    if not archives:
        parser.error("provide archives or --show-log with a matching catalog")
    app = QApplication.instance() or QApplication([])
    output, preview = QWidget(), QWidget()
    output.resize(640, 360)
    preview.resize(480, 270)
    output.show()
    preview.show()
    app.processEvents()
    plugin = MpvPlaybackPlugin(log=lambda message: print(message, file=sys.stderr))
    if not plugin.attach(preview, output):
        raise SystemExit(plugin.errorString())
    failures = 0
    try:
        for archive in archives:
            try:
                result = probe(app, plugin, output, archive, args.speed)
            except Exception as exc:
                result = {"archive": str(archive), "frames": [], "ok": False, "error": str(exc)}
            print(json.dumps(result, sort_keys=True), flush=True)
            failures += not result["ok"]
    finally:
        plugin.shutdown()
        output.close()
        preview.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
