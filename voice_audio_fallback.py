"""A song whose sound the Rust audio engine cannot open (2026-11-16: an MP4 with an AAC variant the decoder calls "too complex") is converted by macOS itself
(`afconvert`, the system's own AAC decoder) into a cached WAV copy, and the Rust engine plays THAT. Before this the song fell back to mpv's own audio, the one
path that skipped for a millisecond here and there on the Intel Mac while every other song, played by the Rust engine, did not.

Only the Rust engine's input changes: mpv still shows the picture of the original file and keeps the clock. The copy lives in <SingWS folder>/cache/voice_wav, one
file per song (name = a hash of its path, size and date, so an edited file converts again), the newest KEEP_FILES kept. Any failure returns None and the caller
falls back to mpv's audio exactly as before: this can never leave a show without sound."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

AFCONVERT = "/usr/bin/afconvert"
KEEP_FILES = 12
TIMEOUT_S = 40.0
_WAV_HEADER_BYTES = 44


def cache_dir() -> Path:
    home = os.environ.get("SINGWS_HOME") or str(Path.home() / "SingWS")
    return Path(home) / "cache" / "voice_wav"


def available() -> bool:
    return sys.platform == "darwin" and os.path.exists(AFCONVERT)


def convert(path: str, log=print, runner=subprocess.run) -> str | None:
    """The path of a WAV copy of `path`'s sound, converting it when no copy exists yet; None when it could not be done."""
    try:
        if not available() or not path:
            return None
        src = Path(path)
        stat = src.stat()
        key = hashlib.sha1(f"{src.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8", "replace")).hexdigest()[:20]
        folder = cache_dir()
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{key}.wav"
        if out.exists() and out.stat().st_size > _WAV_HEADER_BYTES:
            os.utime(out, None)
            log(f"[KARAOKE-RUST] using the converted copy of the sound of {src.name}")
            return str(out)
        tmp = folder / f".{key}.{os.getpid()}.tmp.wav"
        started = time.monotonic()
        result = runner([AFCONVERT, "-f", "WAVE", "-d", "LEI16", str(src), str(tmp)], capture_output=True, timeout=TIMEOUT_S)
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= _WAV_HEADER_BYTES:
            log(f"[KARAOKE-RUST] could not convert the sound of {src.name} (afconvert exit {result.returncode})")
            tmp.unlink(missing_ok=True)
            return None
        os.replace(tmp, out)
        log(f"[KARAOKE-RUST] converted the sound of {src.name} for the Rust engine in {(time.monotonic() - started) * 1000:.0f} ms")
        _prune(folder)
        return str(out)
    except FileNotFoundError:
        return None                                      # nothing to convert: stay silent (the caller logs the fallback itself)
    except Exception as exc:
        try:
            log(f"[KARAOKE-RUST] could not convert the sound: {type(exc).__name__}")
        except Exception:
            pass
        return None


def _prune(folder: Path) -> None:
    try:
        files = sorted((f for f in folder.glob("*.wav") if not f.name.startswith(".")), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[KEEP_FILES:]:
            old.unlink(missing_ok=True)
        for stale in folder.glob(".*.tmp.wav"):
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink(missing_ok=True)
    except Exception:
        pass
