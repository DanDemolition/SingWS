"""Pure transition metadata and conservative boundary policy.

This module deliberately has no Qt or playback-engine imports.  Analysis may
populate records offline; live playback only consumes a matching, versioned
record and must fall back to normal end-of-stream when evidence is incomplete.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
from typing import Iterable, Optional


TRANSITION_ANALYSIS_VERSION = 1
DEFAULT_HOP_SECONDS = 0.1
_MIN_DB = -96.0
_MAX_DB = 6.0

# CD+G packets are 24 bytes; four packets form each 1/75-second sector.
_CDG_PACKET_BYTES = 24
_CDG_PACKETS_PER_SECOND = 300.0
_CDG_COMMAND = 0x09
_CDG_MEMORY_PRESET = 0x01
_CDG_BORDER_PRESET = 0x02
_CDG_TILE_BLOCK = 0x06
_CDG_SCROLL_PRESET = 0x14
_CDG_SCROLL_COPY = 0x18
_CDG_DEFINE_TRANSPARENT = 0x1C
_CDG_LOAD_CLUT_LOW = 0x1E
_CDG_LOAD_CLUT_HIGH = 0x1F
_CDG_TILE_BLOCK_XOR = 0x26


def _finite_float(value, default=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def file_signature(path: str) -> tuple[int, int] | None:
    """Return the same inexpensive identity used by the loudness cache."""
    try:
        stat = os.stat(path)
        return int(stat.st_mtime), int(stat.st_size)
    except OSError:
        return None


def quantize_envelope_db(values: Iterable[float]) -> list[int]:
    """Clamp a lightweight dB envelope to compact integer values."""
    result = []
    for value in values:
        number = _finite_float(value, _MIN_DB)
        result.append(int(round(max(_MIN_DB, min(_MAX_DB, number)))))
    return result


@dataclass
class TransitionAnalysis:
    path: str
    mtime: int
    size: int
    media_kind: str
    duration: float
    analysis_version: int = TRANSITION_ANALYSIS_VERSION
    hop_seconds: float = DEFAULT_HOP_SECONDS
    envelope_db: list[int] = field(default_factory=list)
    integrated_lufs: Optional[float] = None
    peak_db: Optional[float] = None
    audio_start: Optional[float] = None
    audio_end: Optional[float] = None
    fade_start: Optional[float] = None
    fade_confidence: float = 0.0
    visual_start: Optional[float] = None
    visual_end: Optional[float] = None
    visual_confidence: float = 0.0
    visual_method: str = ""
    effective_karaoke_end: Optional[float] = None
    safety_margin: float = 0.0
    safe_for_early_completion: bool = False
    safety_reason: str = "analysis_incomplete"

    def matches_file(self, path: str) -> bool:
        if not self.is_valid():
            return False
        if str(path) != self.path:
            return False
        signature = file_signature(path)
        return signature == (int(self.mtime), int(self.size))

    def is_valid(self, *, duration_tolerance: float = 0.5) -> bool:
        """Reject corrupt or implausible metadata before playback can use it."""
        if self.analysis_version != TRANSITION_ANALYSIS_VERSION:
            return False
        duration = _finite_float(self.duration)
        hop = _finite_float(self.hop_seconds)
        if duration is None or duration <= 0.0 or hop is None or hop <= 0.0:
            return False
        limit = duration + max(0.0, float(duration_tolerance))
        values = {}
        for name in (
            "audio_start", "audio_end", "fade_start", "visual_start",
            "visual_end", "effective_karaoke_end",
        ):
            raw = getattr(self, name)
            if raw is None:
                values[name] = None
                continue
            value = _finite_float(raw)
            if value is None or value < 0.0 or value > limit:
                return False
            values[name] = value
        if (
            values["audio_start"] is not None and values["audio_end"] is not None
            and values["audio_start"] > values["audio_end"]
        ):
            return False
        if (
            values["visual_start"] is not None and values["visual_end"] is not None
            and values["visual_start"] > values["visual_end"]
        ):
            return False
        effective = values["effective_karaoke_end"]
        known_ends = [v for v in (values["audio_end"], values["visual_end"]) if v is not None]
        if effective is not None and known_ends and effective + 1e-6 < max(known_ends):
            return False
        if self.safe_for_early_completion:
            if values["audio_end"] is None or values["visual_end"] is None or effective is None:
                return False
            if effective >= duration:
                return False
        for confidence in (self.fade_confidence, self.visual_confidence):
            value = _finite_float(confidence)
            if value is None or not 0.0 <= value <= 1.0:
                return False
        return True

    def to_dict(self) -> dict:
        payload = asdict(self)
        # Karaoke playback uses only the derived boundaries. Retaining roughly
        # 2,000 envelope samples per song made a full-library JSON cache trend
        # toward a gigabyte with no runtime consumer. BGM keeps its envelope
        # because fade policy may be recalculated from it in future versions.
        payload["envelope_db"] = (
            quantize_envelope_db(self.envelope_db)
            if str(self.media_kind).lower() == "bgm" else []
        )
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "TransitionAnalysis" | None:
        if not isinstance(payload, dict):
            return None
        try:
            record = cls(
                path=str(payload["path"]),
                mtime=int(payload["mtime"]),
                size=int(payload["size"]),
                media_kind=str(payload["media_kind"]),
                duration=float(payload["duration"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        for name in (
            "analysis_version", "hop_seconds", "integrated_lufs", "peak_db",
            "audio_start", "audio_end", "fade_start", "fade_confidence",
            "visual_start", "visual_end", "visual_confidence",
            "visual_method", "effective_karaoke_end", "safety_margin",
            "safe_for_early_completion", "safety_reason",
        ):
            if name in payload:
                setattr(record, name, payload[name])
        try:
            record.analysis_version = int(record.analysis_version)
            record.hop_seconds = float(record.hop_seconds)
            record.duration = float(record.duration)
            record.envelope_db = quantize_envelope_db(payload.get("envelope_db", []))
            record.fade_confidence = float(record.fade_confidence or 0.0)
            record.visual_confidence = float(record.visual_confidence or 0.0)
            record.safety_margin = float(record.safety_margin or 0.0)
            record.safe_for_early_completion = bool(record.safe_for_early_completion)
        except (TypeError, ValueError):
            return None
        if not record.is_valid():
            return None
        return record


class TransitionAnalysisCache:
    """Small atomic JSON store; no background work or implicit analysis."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.checkpoint_path = self.path.with_name(
            self.path.stem + ".checkpoint.jsonl"
        )
        self._records: dict[str, TransitionAnalysis] = {}

    def load(self) -> None:
        self._records.clear()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        rows = payload.get("records", {}) if isinstance(payload, dict) else {}
        if isinstance(rows, dict):
            for key, value in rows.items():
                record = TransitionAnalysis.from_dict(value)
                if record is not None and record.analysis_version == TRANSITION_ANALYSIS_VERSION:
                    self._records[str(key)] = record
        # Replay both sides of an interrupted compaction. Later JSONL rows win,
        # so an audio-only row can be followed by its completed visual merge.
        for checkpoint in (
            self.checkpoint_path.with_suffix(".jsonl.flushing"),
            self.checkpoint_path,
        ):
            try:
                lines = checkpoint.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                    record = TransitionAnalysis.from_dict(row["record"])
                except (KeyError, TypeError, ValueError):
                    continue
                if record is not None and record.analysis_version == TRANSITION_ANALYSIS_VERSION:
                    self._records[record.path] = record

    def get(self, path: str) -> TransitionAnalysis | None:
        record = self._records.get(str(path))
        return record if record is not None and record.matches_file(path) else None

    def put(self, record: TransitionAnalysis) -> None:
        if not isinstance(record, TransitionAnalysis) or not record.is_valid():
            raise ValueError("invalid transition analysis record")
        self._records[record.path] = record

    def append_checkpoint(self, record: TransitionAnalysis) -> None:
        """Durably stage one valid batch result without rewriting the cache."""
        if not isinstance(record, TransitionAnalysis) or not record.is_valid():
            raise ValueError("invalid transition analysis record")
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"record": record.to_dict()}, separators=(",", ":")
            ) + "\n")
            handle.flush()

    def merge_visual_result(
        self,
        *,
        path: str,
        media_kind: str,
        duration: float,
        result: CdgVisualAnalysis | VideoVisualAnalysis,
    ) -> TransitionAnalysis | None:
        """Add visual fields while preserving any cached audio/LUFS fields."""
        signature = file_signature(path)
        total = _finite_float(duration)
        if signature is None or total is None or total <= 0.0:
            return None
        record = self.get(path)
        if record is None:
            record = TransitionAnalysis(
                path=str(path), mtime=signature[0], size=signature[1],
                media_kind=str(media_kind), duration=total,
            )
        else:
            # Preserve previously measured audio and loudness metadata. Duration
            # may become more accurate during the visual decoder pass.
            record.duration = total
            record.media_kind = str(media_kind or record.media_kind)
        record.visual_start = _finite_float(getattr(result, "visual_start", None))
        record.visual_end = _finite_float(getattr(result, "visual_end", None))
        record.visual_confidence = max(
            0.0, min(1.0, _finite_float(getattr(result, "confidence", 0.0), 0.0))
        )
        record.visual_method = str(getattr(result, "method", "") or "")
        record.safety_reason = str(getattr(result, "reason", "visual_analysis_incomplete") or "visual_analysis_incomplete")
        record.safe_for_early_completion = False
        record.effective_karaoke_end = None
        if record.audio_end is not None and bool(getattr(result, "safe_for_early_completion", False)):
            calculate_effective_karaoke_end(record)
        if not record.is_valid():
            return None
        self._records[record.path] = record
        return record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flushing = self.checkpoint_path.with_suffix(".jsonl.flushing")
        payload = {
            "analysis_version": TRANSITION_ANALYSIS_VERSION,
            "records": {key: value.to_dict() for key, value in self._records.items()},
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        # The caller holds the transition persistence lock across this compact
        # save, so no append can race the atomic replacement. Checkpoints are
        # removed only after the complete cache is safely in place.
        for checkpoint in (flushing, self.checkpoint_path):
            try:
                checkpoint.unlink()
            except FileNotFoundError:
                pass


def audio_boundaries_from_envelope(
    envelope_db: Iterable[float],
    *,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
    threshold_db: float = -55.0,
    confirmation_seconds: float = 0.3,
    bridge_quiet_gap_seconds: float = 0.4,
) -> tuple[float | None, float | None]:
    """Find conservative first/last meaningful audio windows.

    A short loud run is required at each edge and small quiet holes between
    confirmed content are bridged.  Fully quiet or malformed input is unknown,
    represented by ``(None, None)`` rather than permission to trim everything.
    """
    hop = _finite_float(hop_seconds, 0.0)
    if hop is None or hop <= 0.0:
        return None, None
    values = [_finite_float(value, _MIN_DB) for value in envelope_db]
    if not values:
        return None, None
    threshold = _finite_float(threshold_db, -55.0)
    required = max(1, int(math.ceil(max(0.0, confirmation_seconds) / hop)))
    loud = [value > threshold for value in values]

    first = None
    run = 0
    for index, active in enumerate(loud):
        run = run + 1 if active else 0
        if run >= required:
            first = index - run + 1
            break
    if first is None:
        return None, None

    last = None
    run = 0
    for index in range(len(loud) - 1, -1, -1):
        run = run + 1 if loud[index] else 0
        if run >= required:
            last = index + run - 1
            break
    if last is None or last < first:
        return None, None

    # The bridge is intentionally explicit even though the edge search already
    # spans interior gaps: it documents that quiet pauses inside the confirmed
    # content range never become a new end boundary.
    bridge_windows = max(0, int(math.ceil(bridge_quiet_gap_seconds / hop)))
    if bridge_windows:
        for index in range(first, last + 1):
            if loud[index]:
                continue
            left = max(first, index - bridge_windows)
            right = min(last + 1, index + bridge_windows + 1)
            if any(loud[left:index]) and any(loud[index + 1:right]):
                loud[index] = True

    return first * hop, min(len(values) * hop, (last + 1) * hop)


def estimate_fade_out_from_envelope(
    envelope_db: Iterable[float],
    *,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
    minimum_fade_seconds: float = 2.0,
) -> tuple[float | None, float]:
    """Estimate only a sustained, mostly descending outro fade.

    This is advisory BGM metadata. It never grants karaoke early completion.
    """
    hop = _finite_float(hop_seconds, 0.0)
    values = [_finite_float(value, _MIN_DB) for value in envelope_db]
    if hop is None or hop <= 0.0 or len(values) < 3:
        return None, 0.0
    _start, audio_end = audio_boundaries_from_envelope(values, hop_seconds=hop)
    if audio_end is None:
        return None, 0.0
    end_index = min(len(values), max(1, int(math.ceil(audio_end / hop))))
    required = max(3, int(math.ceil(max(0.5, minimum_fade_seconds) / hop)))
    if end_index < required:
        return None, 0.0
    # Search backward for the longest window whose smoothed energy loses at
    # least 8 dB and whose local direction is predominantly downward.
    best = None
    for start_index in range(max(0, end_index - int(12.0 / hop)), end_index - required + 1):
        section = values[start_index:end_index]
        if len(section) < required or section[0] - section[-1] < 8.0:
            continue
        downward = sum(1 for left, right in zip(section, section[1:]) if right <= left + 1.0)
        ratio = downward / max(1, len(section) - 1)
        if ratio >= 0.75:
            best = (start_index * hop, min(1.0, 0.65 + (ratio - 0.75) * 1.4))
            break
    return best if best is not None else (None, 0.0)


def build_audio_transition_analysis(
    *, path: str, media_kind: str, duration: float,
    envelope_db: Iterable[float], integrated_lufs=None, peak_db=None,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
) -> TransitionAnalysis | None:
    """Build additive audio metadata without replacing loudness cache data."""
    signature = file_signature(path)
    total = _finite_float(duration)
    if signature is None or total is None or total <= 0.0:
        return None
    envelope = quantize_envelope_db(envelope_db)
    audio_start, audio_end = audio_boundaries_from_envelope(envelope, hop_seconds=hop_seconds)
    fade_start, fade_confidence = estimate_fade_out_from_envelope(
        envelope, hop_seconds=hop_seconds,
    )
    record = TransitionAnalysis(
        path=str(path), mtime=signature[0], size=signature[1],
        media_kind=str(media_kind), duration=total, hop_seconds=hop_seconds,
        envelope_db=envelope, integrated_lufs=_finite_float(integrated_lufs),
        peak_db=_finite_float(peak_db), audio_start=audio_start,
        audio_end=audio_end, fade_start=fade_start,
        fade_confidence=fade_confidence,
    )
    return record if record.is_valid() else None


def build_karaoke_transition_analysis(
    *, path: str, duration: float, audio_start, audio_end,
    integrated_lufs=None, peak_db=None,
) -> TransitionAnalysis | None:
    """Build compact karaoke metadata from already-confirmed audio edges."""
    signature = file_signature(path)
    total = _finite_float(duration)
    start = _finite_float(audio_start)
    end = _finite_float(audio_end)
    if signature is None or total is None or total <= 0.0:
        return None
    record = TransitionAnalysis(
        path=str(path), mtime=signature[0], size=signature[1],
        media_kind="karaoke", duration=total, envelope_db=[],
        integrated_lufs=_finite_float(integrated_lufs),
        peak_db=_finite_float(peak_db), audio_start=start, audio_end=end,
    )
    return record if record.is_valid() else None


def calculate_effective_karaoke_end(
    record: TransitionAnalysis,
    *,
    minimum_visual_confidence: float = 0.85,
    safety_margin: float = 0.3,
) -> float | None:
    """Authorize a karaoke end only from independent audio+visual evidence."""
    audio_end = _finite_float(record.audio_end)
    visual_end = _finite_float(record.visual_end)
    duration = _finite_float(record.duration, 0.0)
    visual_confidence = _finite_float(record.visual_confidence, 0.0)
    margin = max(0.0, _finite_float(safety_margin, 0.3))
    if audio_end is None:
        record.safe_for_early_completion = False
        record.safety_reason = "audio_end_unverified"
        record.effective_karaoke_end = None
        return None
    if visual_end is None or visual_confidence < minimum_visual_confidence:
        record.safe_for_early_completion = False
        record.safety_reason = "visual_end_unverified"
        record.effective_karaoke_end = None
        return None
    effective = max(audio_end, visual_end) + margin
    record.safety_margin = margin
    record.effective_karaoke_end = min(duration, effective) if duration > 0.0 else effective
    # An endpoint at container EOS is valid metadata but offers no early trim.
    record.safe_for_early_completion = duration > 0.0 and record.effective_karaoke_end < duration
    record.safety_reason = "verified_audio_and_visual" if record.safe_for_early_completion else "normal_eos"
    return record.effective_karaoke_end


@dataclass(frozen=True)
class CdgVisualAnalysis:
    visual_start: float | None
    visual_end: float | None
    duration: float
    confidence: float
    method: str
    safe_for_early_completion: bool
    reason: str
    last_change: float | None


@dataclass(frozen=True)
class VideoFrameSample:
    timestamp: float
    mean_luma: float
    difference: float


@dataclass(frozen=True)
class VideoVisualAnalysis:
    visual_start: float | None
    visual_end: float | None
    confidence: float
    method: str
    safe_for_early_completion: bool
    reason: str


@dataclass(frozen=True)
class PreparedSourceIdentity:
    """Identity token that prevents a stale prepared source from being used."""
    path: str
    queue_item_id: str
    generation: int
    mtime: int
    size: int

    @classmethod
    def capture(cls, path: str, queue_item_id: str, generation: int):
        signature = file_signature(path)
        if signature is None or not queue_item_id:
            return None
        return cls(str(path), str(queue_item_id), int(generation), signature[0], signature[1])

    def matches(self, *, path: str, queue_item_id: str, generation: int) -> bool:
        return bool(
            self.path == str(path)
            and self.queue_item_id == str(queue_item_id)
            and self.generation == int(generation)
            and file_signature(path) == (self.mtime, self.size)
        )


def _cdg_packet(command: int, instruction: int, data: bytes = b"") -> bytes:
    """Internal fixture helper kept private so production callers use files."""
    body = bytes(data[:16]).ljust(16, b"\0")
    return bytes((command & 0x3F, instruction & 0x3F, 0, 0)) + body + bytes(4)


def analyze_cdg_visual_bytes(
    payload: bytes,
    *,
    minimum_blank_tail_seconds: float = 1.0,
) -> CdgVisualAnalysis:
    """Find a provably blank CDG tail without guessing lyric semantics.

    Version 1 intentionally authorizes an early visual end only after visible
    non-uniform content is explicitly cleared to a uniform screen and remains
    there. A static non-uniform final screen may be a lyric or title card, so it
    stays unknown and playback must continue to container EOS.

    Tile operations are simulated against the 300x216 indexed framebuffer.
    Palette/scroll operations are tracked as visible mutations, but ambiguous
    final scroll state fails closed unless a later memory preset proves a clear.
    """
    raw = bytes(payload or b"")
    packet_count = len(raw) // _CDG_PACKET_BYTES
    duration = packet_count / _CDG_PACKETS_PER_SECOND
    if packet_count <= 0:
        return CdgVisualAnalysis(None, None, 0.0, 0.0, "cdg_packets_v1", False, "empty_cdg", None)

    width, height = 300, 216
    pixels = bytearray(width * height)
    color_counts = [0] * 16
    color_counts[0] = len(pixels)
    border_color = 0
    palette = [0] * 16
    transparent = None
    first_change = None
    last_change = None
    saw_nonuniform = False
    final_clear_time = None
    ambiguous_after_clear = False

    def timestamp(index: int) -> float:
        return index / _CDG_PACKETS_PER_SECOND

    def changed(index: int):
        nonlocal first_change, last_change
        at = timestamp(index)
        if first_change is None:
            first_change = at
        last_change = at

    for index in range(packet_count):
        packet = raw[index * _CDG_PACKET_BYTES:(index + 1) * _CDG_PACKET_BYTES]
        if (packet[0] & 0x3F) != _CDG_COMMAND:
            continue
        instruction = packet[1] & 0x3F
        data = packet[4:20]

        if instruction == _CDG_MEMORY_PRESET:
            color = data[0] & 0x0F
            if color_counts[color] != len(pixels):
                pixels[:] = bytes((color,)) * len(pixels)
                color_counts[:] = [0] * 16
                color_counts[color] = len(pixels)
                changed(index)
            # A uniform screen after actual graphics is the only v1 proof that
            # a lyric frame was cleared. Repeated presets do not extend it.
            if saw_nonuniform:
                final_clear_time = timestamp(index)
                ambiguous_after_clear = False
        elif instruction == _CDG_BORDER_PRESET:
            color = data[0] & 0x0F
            if color != border_color:
                border_color = color
                changed(index)
        elif instruction in (_CDG_TILE_BLOCK, _CDG_TILE_BLOCK_XOR):
            color0, color1 = data[0] & 0x0F, data[1] & 0x0F
            row, column = data[2] & 0x1F, data[3] & 0x3F
            y0, x0 = row * 12, column * 6
            tile_changed = False
            if y0 < height and x0 < width:
                for tile_y in range(12):
                    y = y0 + tile_y
                    if y >= height:
                        break
                    bits = data[4 + tile_y] & 0x3F
                    offset = y * width + x0
                    for tile_x in range(min(6, width - x0)):
                        color = color1 if bits & (1 << (5 - tile_x)) else color0
                        pos = offset + tile_x
                        if instruction == _CDG_TILE_BLOCK_XOR:
                            color = pixels[pos] ^ color
                        if pixels[pos] != color:
                            color_counts[pixels[pos]] -= 1
                            pixels[pos] = color
                            color_counts[color] += 1
                            tile_changed = True
                if tile_changed:
                    changed(index)
                    if max(color_counts) != len(pixels):
                        saw_nonuniform = True
                        final_clear_time = None
                    elif saw_nonuniform:
                        final_clear_time = timestamp(index)
                        ambiguous_after_clear = False
        elif instruction in (_CDG_SCROLL_PRESET, _CDG_SCROLL_COPY):
            # Full scroll simulation is unnecessary for the safe v1 outcome.
            # Treat it as a visible/ambiguous mutation; a subsequent explicit
            # memory preset can still prove the final blank state.
            changed(index)
            final_clear_time = None
            ambiguous_after_clear = True
        elif instruction in (_CDG_LOAD_CLUT_LOW, _CDG_LOAD_CLUT_HIGH):
            base = 0 if instruction == _CDG_LOAD_CLUT_LOW else 8
            palette_changed = False
            for offset in range(8):
                value = ((data[offset * 2] & 0x3F) << 6) | (data[offset * 2 + 1] & 0x3F)
                if palette[base + offset] != value:
                    palette[base + offset] = value
                    palette_changed = True
            if palette_changed:
                changed(index)
                if final_clear_time is not None:
                    ambiguous_after_clear = True
        elif instruction == _CDG_DEFINE_TRANSPARENT:
            color = data[0] & 0x0F
            if color != transparent:
                transparent = color
                changed(index)
                if final_clear_time is not None:
                    ambiguous_after_clear = True

    blank_tail = duration - final_clear_time if final_clear_time is not None else 0.0
    safe = bool(
        saw_nonuniform
        and final_clear_time is not None
        and not ambiguous_after_clear
        and blank_tail >= max(0.0, float(minimum_blank_tail_seconds))
    )
    if safe:
        reason = "explicit_clear_then_stable_blank_tail"
        visual_end = final_clear_time
        confidence = 0.98
    elif saw_nonuniform and final_clear_time is None:
        reason = "static_or_active_nonblank_final_screen"
        visual_end = None
        confidence = 0.0
    elif final_clear_time is not None:
        reason = "blank_tail_too_short_or_ambiguous"
        visual_end = None
        confidence = 0.0
    else:
        reason = "no_provable_visual_content_and_clear"
        visual_end = None
        confidence = 0.0
    return CdgVisualAnalysis(
        visual_start=first_change,
        visual_end=visual_end,
        duration=duration,
        confidence=confidence,
        method="cdg_packets_v1",
        safe_for_early_completion=safe,
        reason=reason,
        last_change=last_change,
    )


def analyze_cdg_visual(path: str | Path, **kwargs) -> CdgVisualAnalysis:
    try:
        payload = Path(path).read_bytes()
    except OSError:
        return CdgVisualAnalysis(None, None, 0.0, 0.0, "cdg_packets_v1", False, "unreadable_cdg", None)
    return analyze_cdg_visual_bytes(payload, **kwargs)


def analyze_video_tail_samples(
    samples: Iterable[VideoFrameSample],
    *,
    duration: float,
    black_luma_max: float = 0.03,
    static_difference_max: float = 0.01,
    minimum_black_tail_seconds: float = 1.0,
) -> VideoVisualAnalysis:
    """Classify only a high-confidence static-black MP4 tail as dead.

    Inputs are lightweight metrics produced by an offline decoder: normalized
    thumbnail mean luma and frame-to-frame mean absolute difference. A static
    non-black final frame is deliberately ambiguous because it may contain the
    final lyric, a singer cue, or a title card.
    """
    total = _finite_float(duration, 0.0)
    ordered = []
    for sample in samples:
        try:
            timestamp = float(sample.timestamp)
            luma = float(sample.mean_luma)
            difference = float(sample.difference)
        except (AttributeError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (timestamp, luma, difference)):
            continue
        if timestamp < 0.0 or (total > 0.0 and timestamp > total + 0.25):
            continue
        ordered.append(VideoFrameSample(timestamp, luma, difference))
    ordered.sort(key=lambda item: item.timestamp)
    if len(ordered) < 2 or total <= 0.0:
        return VideoVisualAnalysis(None, None, 0.0, "mp4_thumbnails_v1", False, "insufficient_video_samples")

    meaningful = [
        item for item in ordered
        if item.mean_luma > black_luma_max or item.difference > static_difference_max
    ]
    visual_start = meaningful[0].timestamp if meaningful else None

    black_tail_start = None
    for index in range(len(ordered) - 1, -1, -1):
        item = ordered[index]
        is_static_black = (
            item.mean_luma <= black_luma_max
            and item.difference <= static_difference_max
        )
        if not is_static_black:
            break
        black_tail_start = item.timestamp

    tail_length = total - black_tail_start if black_tail_start is not None else 0.0
    has_prior_visual_content = bool(
        meaningful and black_tail_start is not None
        and any(item.timestamp < black_tail_start for item in meaningful)
    )
    safe = bool(
        has_prior_visual_content
        and tail_length >= max(0.0, float(minimum_black_tail_seconds))
    )
    if safe:
        return VideoVisualAnalysis(
            visual_start, black_tail_start, 0.97, "mp4_thumbnails_v1", True,
            "active_video_then_static_black_tail",
        )

    final = ordered[-1]
    if final.mean_luma > black_luma_max and final.difference <= static_difference_max:
        reason = "static_nonblack_final_screen"
    elif black_tail_start is not None:
        reason = "black_tail_too_short_or_no_prior_content"
    else:
        reason = "video_active_or_uncertain_through_end"
    return VideoVisualAnalysis(visual_start, None, 0.0, "mp4_thumbnails_v1", False, reason)


def analyze_mp4_visual_offline(
    path: str,
    *,
    duration: float,
    metric_sampler,
) -> VideoVisualAnalysis:
    """Run an injected thumbnail sampler for a background/backfill worker.

    Keeping the decoder injected leaves this policy module independent from
    libmpv and makes it impossible for playback callers to decode implicitly.
    """
    total = _finite_float(duration, 0.0)
    if total is None or total <= 0.0 or not callable(metric_sampler):
        return VideoVisualAnalysis(
            None, None, 0.0, "mp4_thumbnails_v1", False,
            "invalid_duration_or_sampler",
        )
    try:
        raw_samples = metric_sampler(str(path), duration_seconds=total)
    except Exception:
        return VideoVisualAnalysis(
            None, None, 0.0, "mp4_thumbnails_v1", False,
            "video_decoder_failed",
        )
    samples = []
    for row in raw_samples or ():
        try:
            samples.append(VideoFrameSample(
                timestamp=float(row["timestamp"]),
                mean_luma=float(row["mean_luma"]),
                difference=float(row["difference"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return analyze_video_tail_samples(samples, duration=total)


def select_bgm_crossfade_seconds(
    record: TransitionAnalysis | None,
    *,
    default_seconds: float = 5.0,
) -> tuple[float, str]:
    """Choose a bounded BGM crossfade from cached metadata only.

    This policy never analyzes or seeks media. Missing/old metadata keeps the
    established five-second behavior. A verified dead tail advances promptly;
    a confident natural fade gets a longer overlap; a hard ending gets a short
    transition. The player remains responsible for generation cancellation.
    """
    fallback = max(2.0, min(8.0, _finite_float(default_seconds, 5.0)))
    if record is None or record.analysis_version != TRANSITION_ANALYSIS_VERSION:
        return fallback, "metadata_unavailable"
    duration = _finite_float(record.duration, 0.0)
    audio_end = _finite_float(record.audio_end)
    fade_start = _finite_float(record.fade_start)
    fade_confidence = _finite_float(record.fade_confidence, 0.0)
    if duration <= 0.0 or audio_end is None:
        return fallback, "audio_boundary_unavailable"
    dead_tail = max(0.0, duration - audio_end)
    if dead_tail >= 1.0:
        return 2.0, "verified_dead_tail"
    if fade_start is not None and fade_confidence >= 0.8:
        fade_length = max(0.0, audio_end - fade_start)
        return max(4.0, min(8.0, fade_length)), "natural_fade"
    if fade_confidence <= 0.2:
        return 2.5, "hard_ending"
    return fallback, "typical_ending"
