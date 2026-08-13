"""Small media utilities shared by playback and analysis jobs."""

from __future__ import annotations

NS_PER_SECOND = 1_000_000_000


def _normalized_audio_device_name(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def match_qt_audio_device(devices, wanted_name: str):
    wanted = _normalized_audio_device_name(wanted_name)
    if not wanted:
        return None
    candidates = []
    for device in list(devices or []):
        try:
            name = str(device.description() or "")
        except Exception:
            continue
        key = _normalized_audio_device_name(name)
        if not key:
            continue
        if key == wanted:
            return device
        if wanted in key or key in wanted:
            candidates.append(device)
    return candidates[0] if len(candidates) == 1 else None


def probe_duration_seconds(path: str) -> float:
    try:
        from mutagen import File as MutagenFile
        media = MutagenFile(str(path))
        return max(0.0, float(getattr(getattr(media, "info", None), "length", 0.0) or 0.0))
    except Exception:
        return 0.0
