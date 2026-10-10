"""Python front end for the Rust analysis helper (``singws-analyze``).

The helper is a separate, short-lived program (``rust/crates/singws-analysis``): one process per file, no shared memory
with the app, killed on timeout or cancel. It returns the numbers the app currently gets from libmpv jobs (integrated
loudness, sample peak, duration, first/last audible time, optional 100 ms RMS envelope).

Three engines, chosen by the ``analysis_engine`` setting (default ``"libmpv"``, i.e. nothing here runs):

* ``"libmpv"`` - today's behaviour, untouched.
* ``"shadow"`` - libmpv stays authoritative. Rust runs alongside it, differences are logged (``[ANALYSIS-SHADOW]``) and
  summarised, and the Rust answer is never used. Failures in Rust cannot affect the result.
* ``"rust"`` - Rust answers the requests it is verified for; for everything else, and on ANY Rust failure (missing helper,
  timeout, unsupported or damaged file) the libmpv path runs, so failure semantics stay libmpv's.

``RUST_*_VERIFIED`` below record which result kinds have been checked against libmpv on real files; ``"rust"`` mode only
trusts those.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

HELPER_NAME = "singws-analyze"
ENGINES = ("libmpv", "shadow", "rust")
TIMEOUT_GRACE_S = 5.0     # extra wall-clock beyond the helper's own --timeout before the process is killed

# Result kinds the Rust helper has been verified for against libmpv on real library files
# (tools/rust_analysis_parity.py). "rust" mode uses Rust only for verified kinds.
RUST_LOUDNESS_VERIFIED = True        # integrated loudness + sample peak: 258/258 and 500/500 files within 0.1 LU
RUST_BOUNDARIES_VERIFIED = True      # duration + first/last audible time: 150/150 files identical to libmpv (max difference 0.000 s)
RUST_ENVELOPE_VERIFIED = True        # 100 ms RMS envelope. Was False while libmpv's 4800-sample windows were 109 ms at 44.1 kHz; since the
                                     # 2026-10-09 aresample=48000 fix both are true 100 ms. 2026-10-10, 40 real tracks: derived audio_start
                                     # identical (40/40), audio_end and fade_start within 0.1 s, envelope length ratio 0.9992-1.0000.

# Shadow comparison tolerances (what counts as a disagreement worth a log line).
LU_TOLERANCE = 0.1001
PEAK_TOLERANCE = 0.1001
BOUNDARY_TOLERANCE_S = 0.1
DURATION_TOLERANCE_S = 0.05
ENVELOPE_MEAN_TOLERANCE_DB = 1.0


class RustAnalysisError(RuntimeError):
    """The Rust helper could not give an answer. ``helper_fault`` is True when the helper itself misbehaved
    (would not start, crashed, timed out, printed nothing usable) rather than reporting a problem with this one file."""

    def __init__(self, message: str, *, helper_fault: bool = False, code: str = ""):
        super().__init__(message)
        self.helper_fault = helper_fault
        self.code = code


def normalize_engine(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in ENGINES else "libmpv"


def _arch_dir() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def find_helper() -> Path | None:
    """The helper executable: ``SINGWS_ANALYZE_BIN`` override, then the app bundle, then the source tree."""
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_ANALYZE_BIN")
    if override:
        candidates.append(Path(override))
    roots = [
        getattr(sys, "_MEIPASS", None),
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
        Path(__file__).resolve().parent,
    ]
    for root in roots:
        if root:
            root = Path(root)
            candidates += [
                root / HELPER_NAME,
                root / "Frameworks" / HELPER_NAME,
                root / "native" / "singws_analyze" / _arch_dir() / HELPER_NAME,
            ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _kill(process) -> None:
    """Stop the helper and close its pipes, so a cancelled or timed-out request leaks no file handle."""
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.communicate(timeout=5.0)
    except Exception:
        process.wait()


def run_helper(
    source: str, *, timeout: float = 120.0, want_envelope: bool = False, cancel_check=None,
    low_priority: bool = False, helper: Path | None = None,
) -> dict:
    """Analyse one file; return the helper's JSON record. Raises RustAnalysisError / TimeoutError / InterruptedError."""
    exe = helper or find_helper()
    if exe is None:
        raise RustAnalysisError("analysis helper not found", helper_fault=True, code="missing")
    command: list[str] = []
    if low_priority and os.path.exists("/usr/bin/nice"):
        command += ["/usr/bin/nice", "-n", "10"]   # a niced burst keeps a live show's GUI and audio threads ahead of it
    command += [str(exe), "--jobs", "1", "--timeout", f"{max(1.0, float(timeout)):.1f}"]
    if want_envelope:
        command.append("--envelope")
    src = str(source)
    command.append("./" + src if src.startswith("-") else src)
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise RustAnalysisError(f"analysis helper would not start: {exc}", helper_fault=True, code="spawn") from exc
    deadline = time.monotonic() + max(1.0, float(timeout)) + TIMEOUT_GRACE_S
    output = b""
    while True:
        if cancel_check is not None and cancel_check():
            _kill(process)
            raise InterruptedError("analysis helper request cancelled")
        if time.monotonic() >= deadline:
            _kill(process)
            raise TimeoutError("analysis helper timed out")
        try:
            output, _ = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            continue
    record = None
    for line in reversed(output.decode("utf-8", "replace").splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and "ok" in candidate:
            record = candidate
            break
    if record is None:
        raise RustAnalysisError(
            f"analysis helper produced no result (exit {process.returncode})", helper_fault=True, code="no_output")
    if not record.get("ok"):
        raise RustAnalysisError(
            f"{record.get('error', 'error')}: {str(record.get('detail', ''))[:200]}",
            helper_fault=str(record.get("error")) == "internal", code=str(record.get("error") or ""))
    return record


def _num(value):
    return None if value is None else float(value)


class RustAnalysisSession:
    """Same call surface as ``libmpv_media_jobs.IsolatedLoudnessSession`` for the requests Rust can answer."""

    isolated = True
    _MAX_CONSECUTIVE_FAULTS = 3

    def __init__(self, helper: Path | None = None, *, low_priority: bool = False):
        self._helper = helper
        self._low_priority = low_priority
        self._faults = 0
        self._disabled = False

    @property
    def usable(self) -> bool:
        return not self._disabled and (self._helper is not None or find_helper() is not None)

    def _call(self, source, timeout, want_envelope=False, cancel_check=None) -> dict:
        if self._disabled:
            raise RustAnalysisError("rust analysis disabled after repeated helper faults", helper_fault=True, code="disabled")
        try:
            record = run_helper(
                source, timeout=timeout, want_envelope=want_envelope, cancel_check=cancel_check,
                low_priority=self._low_priority, helper=self._helper)
        except RustAnalysisError as exc:
            if exc.helper_fault:
                self._note_fault()
            else:
                self._faults = 0      # the helper answered; the file itself was the problem
            raise
        except InterruptedError:
            raise
        except Exception as exc:       # timeout and anything unexpected count against the helper
            self._note_fault()
            raise RustAnalysisError(str(exc) or exc.__class__.__name__, helper_fault=True, code="fault") from exc
        self._faults = 0
        return record

    def _note_fault(self):
        self._faults += 1
        if self._faults >= self._MAX_CONSECUTIVE_FAULTS:
            self._disabled = True

    def measure(self, source: str, *, timeout: float = 120.0, cancel_check=None):
        r = self._call(source, timeout, cancel_check=cancel_check)
        return _num(r.get("i")), _num(r.get("peak_db"))

    def measure_karaoke_transition(self, source: str, *, timeout: float = 120.0, cancel_check=None):
        r = self._call(source, timeout, cancel_check=cancel_check)
        return _num(r.get("i")), _num(r.get("peak_db")), _num(r.get("duration")), _num(r.get("start")), _num(r.get("end"))

    def measure_transition(self, source: str, *, timeout: float = 120.0, cancel_check=None):
        r = self._call(source, timeout, want_envelope=True, cancel_check=cancel_check)
        envelope = [float(v) for v in (r.get("envelope") or [])]
        if not envelope:
            raise RustAnalysisError("no RMS envelope in the helper result", code="no_envelope")
        return _num(r.get("i")), _num(r.get("peak_db")), envelope

    def close(self):
        return None


# ---------------------------------------------------------------------------------------------------------------------
# Shadow comparison
# ---------------------------------------------------------------------------------------------------------------------

def compare_results(kind: str, primary, rust) -> list[str]:
    """Human-readable disagreements between libmpv (``primary``) and Rust results of the same ``kind``
    (``"loudness"``, ``"karaoke"`` or ``"bgm"``). Empty when they agree within tolerance."""
    problems: list[str] = []

    def pair(name, a, b, tol):
        if a is None and b is None:
            return
        if a is None or b is None:
            problems.append(f"{name}: libmpv={a} rust={b}")
        elif abs(float(a) - float(b)) > tol:
            problems.append(f"{name}: libmpv={float(a):.2f} rust={float(b):.2f} (d={float(b) - float(a):+.2f})")

    pair("lufs", primary[0], rust[0], LU_TOLERANCE)
    pair("peak", primary[1], rust[1], PEAK_TOLERANCE)
    if kind == "karaoke":
        pair("duration", primary[2], rust[2], DURATION_TOLERANCE_S)
        pair("audio_start", primary[3], rust[3], BOUNDARY_TOLERANCE_S)
        pair("audio_end", primary[4], rust[4], BOUNDARY_TOLERANCE_S)
    elif kind == "bgm":
        a, b = list(primary[2] or []), list(rust[2] or [])
        if a and b:
            if abs(len(a) - len(b)) > max(2, 0.02 * len(a)):
                problems.append(f"envelope length: libmpv={len(a)} rust={len(b)}")
            n = min(len(a), len(b))
            mean_abs = sum(abs(a[i] - b[i]) for i in range(n)) / n
            if mean_abs > ENVELOPE_MEAN_TOLERANCE_DB:
                problems.append(f"envelope mean |diff|={mean_abs:.2f} dB over {n} windows")
        elif bool(a) != bool(b):
            problems.append("envelope present in only one result")
    return problems


class ShadowStats:
    """Running totals for the comparison, logged as a short summary every ``every`` comparisons."""

    def __init__(self, every: int = 25):
        self.every = every
        self.compared = 0
        self.mismatched = 0
        self.rust_errors = 0
        self.primary_s = 0.0
        self.rust_s = 0.0
        self._lock = threading.Lock()

    def add(self, *, mismatch: bool, rust_error: bool, primary_s: float, rust_s: float) -> str | None:
        with self._lock:
            self.compared += 1
            self.mismatched += 1 if mismatch else 0
            self.rust_errors += 1 if rust_error else 0
            self.primary_s += primary_s
            self.rust_s += rust_s
            if self.compared % self.every:
                return None
            speed = (self.primary_s / self.rust_s) if self.rust_s > 0 else 0.0
            return (f"[ANALYSIS-SHADOW] summary compared={self.compared} mismatched={self.mismatched} "
                    f"rust_errors={self.rust_errors} libmpv_avg={self.primary_s / self.compared * 1000:.0f}ms "
                    f"rust_avg={self.rust_s / self.compared * 1000:.0f}ms (rust {speed:.1f}x)")


SHADOW_STATS = ShadowStats()


def shadow_run(kind: str, source: str, primary_call, rust_call, *, log=None, stats: ShadowStats | None = None,
               label: str = ""):
    """Run ``primary_call()`` (authoritative) with ``rust_call()`` alongside it; return the primary result untouched.

    The Rust call runs on its own thread so the comparison costs about as much time as the slower engine, not both.
    Any Rust error is logged and swallowed; an error from the primary call propagates exactly as it would without shadowing.
    """
    log = log or (lambda _m: None)
    stats = stats if stats is not None else SHADOW_STATS
    box: dict = {}

    def _rust():
        t0 = time.monotonic()
        try:
            box["result"] = rust_call()
        except BaseException as exc:      # never let the shadow thread raise
            box["error"] = exc
        box["seconds"] = time.monotonic() - t0

    thread = threading.Thread(target=_rust, name="analysis-shadow", daemon=True)
    thread.start()
    t0 = time.monotonic()
    try:
        primary = primary_call()
    finally:
        primary_s = time.monotonic() - t0
        thread.join(timeout=60.0)
    name = label or os.path.basename(str(source))
    mismatch = False
    rust_error = "error" in box or "result" not in box
    if rust_error:
        reason = box.get("error", "no result")
        log(f"[ANALYSIS-SHADOW] rust failed file={name!r} kind={kind}: {reason}")
    elif primary is not None and primary[0] is not None:
        problems = compare_results(kind, primary, box["result"])
        if problems:
            mismatch = True
            log(f"[ANALYSIS-SHADOW] MISMATCH file={name!r} kind={kind}: " + "; ".join(problems))
    summary = stats.add(mismatch=mismatch, rust_error=rust_error, primary_s=primary_s, rust_s=box.get("seconds", 0.0))
    if summary:
        log(summary)
    return primary


# ---------------------------------------------------------------------------------------------------------------------
# Session wrapper used by the library scan
# ---------------------------------------------------------------------------------------------------------------------

class HybridAnalysisSession:
    """Wraps the libmpv helper session (``primary``); consults Rust according to ``engine`` ("shadow" or "rust")."""

    def __init__(self, primary, rust: RustAnalysisSession, engine: str, log=None):
        self._primary = primary
        self._rust = rust
        self._engine = normalize_engine(engine)
        self._log = log or (lambda _m: None)

    @property
    def usable(self) -> bool:
        return self._primary.usable

    @property
    def isolated(self) -> bool:
        return bool(getattr(self._primary, "isolated", False))

    def _route(self, kind, verified, source, timeout, primary_call, rust_call):
        if self._engine == "shadow" and self._rust.usable:
            return shadow_run(kind, source, primary_call, rust_call, log=self._log)
        if self._engine == "rust" and verified and self._rust.usable:
            try:
                result = rust_call()
                if result is not None and result[0] is not None:
                    return result
                self._log(f"[ANALYSIS] rust gave no loudness for {os.path.basename(str(source))!r}; using libmpv")
            except (InterruptedError, KeyboardInterrupt):
                raise
            except Exception as exc:
                self._log(f"[ANALYSIS] rust failed for {os.path.basename(str(source))!r} ({exc}); using libmpv")
        return primary_call()

    def measure(self, source, *, timeout=120.0):
        return self._route("loudness", RUST_LOUDNESS_VERIFIED, source, timeout,
                           lambda: self._primary.measure(source, timeout=timeout),
                           lambda: self._rust.measure(source, timeout=timeout))

    def measure_karaoke_transition(self, source, *, timeout=120.0):
        return self._route("karaoke", RUST_BOUNDARIES_VERIFIED and RUST_LOUDNESS_VERIFIED, source, timeout,
                           lambda: self._primary.measure_karaoke_transition(source, timeout=timeout),
                           lambda: self._rust.measure_karaoke_transition(source, timeout=timeout))

    def measure_transition(self, source, *, timeout=120.0):
        return self._route("bgm", RUST_ENVELOPE_VERIFIED and RUST_LOUDNESS_VERIFIED, source, timeout,
                           lambda: self._primary.measure_transition(source, timeout=timeout),
                           lambda: self._rust.measure_transition(source, timeout=timeout))

    def measure_fast(self, source, *, timeout=120.0):
        return self._primary.measure_fast(source, timeout=timeout)       # sampled analysis stays on libmpv

    def measure_video_tail(self, *args, **kwargs):
        return self._primary.measure_video_tail(*args, **kwargs)          # video analysis stays on libmpv

    def close(self):
        try:
            self._primary.close()
        finally:
            self._rust.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def make_session(engine, primary_factory, *, log=None):
    """The scan's analysis session: the plain libmpv helper session unless Rust is requested AND available."""
    primary = primary_factory()
    kind = normalize_engine(engine)
    if kind == "libmpv":
        return primary
    rust = RustAnalysisSession()
    if not rust.usable:
        if log:
            log(f"[ANALYSIS] analysis_engine={kind} requested but the Rust helper was not found; using libmpv")
        return primary
    if log:
        log(f"[ANALYSIS] analysis_engine={kind} (helper {find_helper()})")
    return HybridAnalysisSession(primary, rust, kind, log=log)
