"""Seek guard for CDG songs whose graphics stop long before the audio does.

A CDG picture only has frames where the file has a command packet. When the last frame before the final one is more than MIN_GAP_SECONDS earlier, an
exact seek into that gap makes mpv wait for the first frame after the target, which is the final one: the picture jumps to the end and the song ends
(docs/design/stage-e-no-python-host-spike-2.md). Clamping the seek target to the last frame before the gap keeps picture and sound together.
Rust twin: singws-cdg `dead_zones`.
"""
PACKET_BYTES = 24
PACKETS_PER_SECOND = 300.0
MIN_GAP_SECONDS = 1.0
MID_GAP_SECONDS = 5.0   # a quiet stretch inside the song counts from this length (an ordinary page change is well under it)
_COMMAND = 9
_MASK = 0x3F
_TILE_BLOCK = 6
_TILE_XOR = 38


_COMMAND_FIRST = bytes(1 if (b & _MASK) == _COMMAND else 0 for b in range(256))


def frame_times(data: bytes):
    """Seconds of every packet that makes a frame: any CDG command, except a tile placed outside the picture (FFmpeg drops that frame).

    Runs on the GUI thread when a song starts, so it never looks at the packets that are not commands: a mask of every packet's first byte is searched
    for the command markers (the old per-packet loop took ~0.7 s on a 4-minute song on the Intel Mac)."""
    out = []
    n = len(data) // PACKET_BYTES
    marks = data[0:n * PACKET_BYTES:PACKET_BYTES].translate(_COMMAND_FIRST)
    find = marks.find
    i = find(1)
    while i != -1:
        base = i * PACKET_BYTES
        if (data[base + 1] & _MASK) in (_TILE_BLOCK, _TILE_XOR) and ((data[base + 6] & 0x1F) * 12 > 204 or (data[base + 7] & 0x3F) * 6 > 294):
            pass
        else:
            out.append(i / PACKETS_PER_SECOND)
        i = find(1, i + 1)
    return out


def terminal_gap(data: bytes):
    """(last frame before the gap, first frame after it) when the graphics go quiet for more than MIN_GAP_SECONDS and only a closing burst
    (within MIN_GAP_SECONDS of the end of the frames) follows; None for a song without such a tail."""
    times = frame_times(data)
    for i in range(len(times) - 2, -1, -1):
        if times[i + 1] - times[i] > MIN_GAP_SECONDS:
            return (times[i], times[i + 1]) if times[-1] - times[i + 1] <= MIN_GAP_SECONDS else None
    return None


def dead_zones(data: bytes):
    """Every stretch where a seek would jump ahead: each frame-less stretch longer than MID_GAP_SECONDS, plus the terminal gap (see `terminal_gap`)."""
    times = frame_times(data)
    zones = [(a, b) for a, b in zip(times, times[1:]) if b - a > MID_GAP_SECONDS]
    tail = terminal_gap(data)
    if tail is not None and tail not in zones:
        zones.append(tail)
    return sorted(zones)


def clamp_seek(zones, seconds: float) -> float:
    """The seek target to use: inside a dead zone it moves back to the last frame before it. `zones` is a list of (before, after), a single pair, or None."""
    if zones and isinstance(zones[0], float):
        zones = [zones]
    for before, after in zones or ():
        if before < seconds < after:
            return before
    return seconds


def clamp_seek_zone(zones, seconds: float):
    """Like `clamp_seek`, but also answers which stretch was used: ``(target, (before, after))``, or ``(seconds, None)`` when the seek was left alone."""
    if zones and isinstance(zones[0], float):
        zones = [zones]
    for before, after in zones or ():
        if before < seconds < after:
            return before, (before, after)
    return seconds, None


def load_gap(path):
    try:
        with open(path, "rb") as fh:
            return dead_zones(fh.read())
    except Exception:
        return None
