# Rust prototype design: `singws-analysis` (Phase 5)

Companion to `RUST_ARCHITECTURE_AUDIT.md` and `RUST_MIGRATION_PLAN.md` (Stage 1).
Status: **design only. Nothing here is implemented; implementation needs explicit approval.**
Labels: **[VERIFIED]** read/measured in this audit, **[PROPOSED]** design choice, **[OPEN]** unresolved.

---

## 1. Why this is the smallest useful, safe subsystem

Selection criteria from the brief: isolated from production, demonstrates Python integration, automated tests, benchmarks, builds for both Macs, no change to playback behaviour, clear path to integration.

`singws-analysis` fits because:
* It is **not in the playback path**. It runs on library scans and next-up lookups, so a defect cannot silence or black-screen a show. (The audio engine, `bridge.mm`, BASS and libmpv playback are untouched.)
* It has a **ready-made oracle**: the existing libmpv results. `~/SingWS/loudness.json` is a dict `path -> {"i": LUFS, "peak_db": dBFS, "mtime", "size", "mode"}` [VERIFIED, 261 entries on this Mac; the venue Mac's cache is far larger [REPORTED ~14 k]]. Silence/boundary metadata from `transition_analysis.py` provides a second oracle.
* The current implementation has a **documented cost to beat**: one libmpv core per job, recyclable helper processes (`libmpv_media_jobs.py`: `OfflineMpvJob`, `IsolatedLoudnessSession`, `run_isolated_analysis_worker`), a past ~1 MB/track memory leak, and Turbo throughput of ~1.7-2.2 tracks/s on the six-core Intel Mac [REPORTED]. These are measurable.
* It needs **no schema change**: results flow through the existing cache writers.
* It exercises the exact toolchain questions every later stage needs: Rust on Intel + Apple Silicon at macOS 12.3, Python integration, PyInstaller packaging, signing.

---

## 2. Scope

### In scope [PROPOSED]
1. Decode MP3, FLAC, WAV, M4A/AAC, OGG (what the library contains) to f32 PCM, streaming in bounded chunks.
2. Open MP3+G archives (`.zip` with one MP3 + one CDG; Deflate and, if feasible, Deflate64) and decode the MP3 member without writing a temp file.
3. Compute, in a single pass:
   * ITU-R BS.1770 / EBU R128 **integrated loudness** (gated) and **sample peak** in dBFS (the cache stores sample peak; `measure_loudness_lufs` is documented to return "sample peak dBFS" [VERIFIED]);
   * duration;
   * **silence boundaries**: first audible time and last audible time using the same thresholds the Python code uses (derive from `_configure_karaoke_transition_job` / `_parse_karaoke_boundaries` in `libmpv_media_jobs.py`);
   * a coarse RMS envelope for BGM fade analysis (100 ms windows, matching the current compact path).
4. Return a plain struct; never write caches itself.
5. Strict resource limits: max decoded duration, max compressed size, cancellation flag, per-file timeout, no panics across the boundary.

### Out of scope
Video tail visual analysis (`sample_video_tail_metrics`), CDG decoding, KaraFun, any playback, any DSP applied to audio output, any change to cache formats or settings keys beyond one new selector.

---

## 3. Public interface

### 3.1 Rust API (crate `singws-analysis`)
```rust
pub struct AnalysisOptions { pub want_envelope: bool, pub noise_floor_db: f32, pub lead_min_s: f32, pub max_seconds: f32 }
pub struct AnalysisResult {
    pub integrated_lufs: Option<f64>,   // None if gated silence / too short
    pub sample_peak_dbfs: f64,
    pub duration_s: f64,
    pub audio_start_s: Option<f64>,
    pub audio_end_s: Option<f64>,
    pub envelope_100ms: Option<Vec<f32>>,
    pub engine_version: u32,
}
pub enum AnalysisError { Unreadable, Unsupported, NoAudio, Corrupt(String), TooLong, Cancelled }
pub fn analyze_path(path: &Path, opts: &AnalysisOptions, cancel: &AtomicBool) -> Result<AnalysisResult, AnalysisError>;
```
Error categories map onto the existing cache semantics: `AnalysisTrackError` (skip this track, do not cache a permanent failure) vs. structural failures (cached against size/mtime).

### 3.2 Python surface [PROPOSED]
A module `singws_analysis` (PyO3, built by maturin, `abi3` if the Python 3.14 toolchain permits [OPEN: verify abi3/3.14 support in the PyO3 version selected]) exposing:
```python
analyze(path: str, want_envelope: bool = False, timeout_s: float = 120.0) -> dict  # keys: i, peak_db, duration, start, end, envelope
analyze_batch(paths: list[str], workers: int, ...) -> iterator  # releases the GIL, yields results as they finish
engine_version() -> int
```
Fallback if PyO3 packaging proves awkward: a small CLI (`singws-analyze --json <path>`, line-protocol batch mode on stdin/stdout) driven by the same isolated-helper pattern the app already uses. The Rust crate is identical either way.

### 3.3 Integration seam (later, not in the prototype)
`libmpv_media_jobs.measure_loudness_lufs()` and the karaoke-boundary job gain an alternative provider selected by a new setting `analysis_engine = "libmpv" | "rust"` (default `"libmpv"`). Cache entries gain an `engine` tag. The prototype itself does **not** edit those files.

---

## 4. Design details

* **Decode:** `symphonia` (pure Rust MP3/FLAC/WAV/AAC/OGG demux+decode). Known risk: handling of damaged MP3s the app has met (decoder "Header missing" storms, mid-stream format changes). Policy: a decode error mid-stream is tolerated up to a bounded count and reported as `Corrupt` only if no usable audio remains; behaviour to be tuned against the corrupt-file corpus.
* **Loudness:** the `ebur128` crate (a port of libebur128, the same algorithm family as ffmpeg's `ebur128` filter that libmpv uses today [VERIFIED by reading `_configure_ebur128_job`]). Mode: integrated + sample peak. K-weighting/gating are not reimplemented.
* **Resampling:** none; `ebur128` handles the arbitrary sample rate natively.
* **Boundaries/envelope:** straightforward windowed RMS with the same dB thresholds and hold times as the Python parser; implemented in-crate with unit tests.
* **ZIP:** `zip` crate with deflate; Deflate64 support is a known gap [OPEN]: the library was repacked to standard Deflate on 2026-08-30 so it may not be needed; unsupported methods return `Unsupported` (the caller falls back to libmpv).
* **Concurrency:** `rayon` thread pool for `analyze_batch`; work-stealing across files; per-file decode is single-threaded; memory bounded by chunked streaming (target < 30 MB per in-flight file).
* **Safety:** no `unsafe` in the analysis logic; `#![forbid(unsafe_code)]` in the crate; FFI/PyO3 glue is the only unsafe-adjacent code, wrapped in `catch_unwind`.
* **Determinism:** fixed chunk size and a documented float-accumulation order so repeated runs give identical output.
* **Versioning:** `engine_version` increments whenever results could change, letting the app re-run old entries.

---

## 5. Repository layout [PROPOSED]

```
rust/                       # new top-level directory; nothing outside it changes in the prototype
  Cargo.toml                # workspace (resolver 2), pinned toolchain via rust-toolchain.toml
  crates/singws-analysis/   # library + criterion benches + tests
  crates/singws-py/         # PyO3 module (maturin) [or crates/singws-analyze-cli/]
  corpus/README.md          # how to build the golden corpus (media is NOT committed)
tools/rust_analysis_compare.py   # oracle comparison + benchmark harness (new file, not imported by the app)
```
No change to `0.2.18.1.py`, the specs, the bridge, settings or caches in the prototype. The Python import is exercised only from the test/benchmark scripts.

---

## 6. Dependencies (each justified, per `AGENTS.md`)

| Dependency | Build/runtime | Why | Licence/notes |
| --- | --- | --- | --- |
| Rust stable toolchain (rustup, `x86_64-apple-darwin` + `aarch64-apple-darwin`) | build-time only. **Not installed on this Mac today** [VERIFIED]. | Compile the crate for both architectures. | MIT/Apache-2.0. |
| `symphonia` | runtime (statically linked) | Pure-Rust decoders; no libmpv per track. | MPL-2.0 (file-level copyleft; static linking is fine, note in `LICENSES`). |
| `ebur128` | runtime (static) | BS.1770 loudness. | MIT. |
| `zip` | runtime (static) | MP3+G archive access. | MIT. |
| `rayon` | runtime (static) | Batch parallelism. | MIT/Apache-2.0. |
| `pyo3` + `maturin` | build-time (PyO3 is linked); maturin build-only | Python binding. | Apache-2.0/MIT. Alternative: CLI, no PyO3. |
| `criterion`, `proptest` | dev only | Benchmarks, property tests. | MIT/Apache-2.0. |

No paid or proprietary component. No new runtime dependency outside the compiled artifact. Dependency versions are locked by `Cargo.lock`, vendored with `cargo vendor` for offline/reproducible builds [PROPOSED].

---

## 7. Tests (automated)

1. **Unit:** gating edge cases (silence-only file -> `integrated_lufs = None`), clipping, mono/stereo/5.1 downmix, sample-rate coverage (22.05/32/44.1/48 kHz), very short files, truncated files, zero-length.
2. **Reference signals:** generated tones and EBU Tech 3341/3342-style test signals with known LUFS; asserts within +-0.05 LU.
3. **Oracle corpus (golden):** build from real files already measured by libmpv. Sources: this Mac's `~/SingWS/loudness.json` (261 entries [VERIFIED]); optionally a copy of the venue Mac's cache (operator decision [OPEN]). Script `tools/rust_analysis_compare.py` runs both engines, reports per-file deltas and the percentage inside tolerance. Media is not committed.
4. **Corrupt-file corpus:** truncated MP3, bad header, wrong extension, ZIP with `__MACOSX` junk, ZIP without MP3, Deflate64 ZIP, 0-byte file. Expectation: no panic/crash/hang; categorised errors; matches the existing structural-failure semantics.
5. **Boundary tests:** silence boundaries vs the Python parser on the same decoded audio, +-50 ms.
6. **Property tests (`proptest`):** result invariants (peak >= any sample, duration monotonic with truncation, start <= end).
7. **Python integration tests:** import from a venv, call `analyze`/`analyze_batch`, check types, GIL release (concurrent Python thread keeps ticking during a long batch), cancellation, timeout.
8. **Packaging smoke test:** build the module, import it inside a throw-away PyInstaller one-file test app on both architectures (no change to the real specs).

All run with a scratch `SINGWS_HOME` where the app is imported; the Rust tests never touch `~/SingWS` (the oracle script reads a **copy** of the cache).

---

## 8. Benchmarks

`criterion` benches and a Python harness measure, on this Mac (arm64, native) and on Intel (x86_64 native on the venue Mac; Rosetta here is **not** valid for performance numbers):

| Metric | Baseline (libmpv helper path) | Target |
| --- | --- | --- |
| Single-track latency (3-4 min MP3) | measured by harness | no worse than baseline |
| Throughput, 4 workers, 500-track sample | ~2.16 tracks/s on the six-core Intel Mac [REPORTED] | >= 2x |
| Peak RSS over 5,000 tracks | historical leak class ~1 MB/track [REPORTED, fixed in helper] | growth < 5 MB total |
| CPU time per track | measured | lower |
| Failure rate on the corpus | measured | not higher |

---

## 9. Build for both architectures

* `cargo build --release --target aarch64-apple-darwin` and `--target x86_64-apple-darwin` on the Apple Silicon Mac (both targets are supported from one host); `MACOSX_DEPLOYMENT_TARGET=12.3` and `-C target-cpu` left at the generic default so the Intel build runs on older Intel CPUs.
* PyO3 module: `maturin build --release --target <triple>` per architecture inside the matching venv (`.venv` for arm64; `.venv-build-intel-rosetta` for x86_64, mirroring how the Intel app is built today).
* Verification with the existing tools: `tools/verify_macos_arch.py --require <arch>`, `tools/verify_macos_min_version.py --arch <arch> --maximum 12.3`.
* Signing: ad-hoc/`SingWS Local Code Signing` identity exactly like the bridge dylibs, as part of a future packaging step (not the prototype).
* Intel **performance** is measured only on the venue Intel Mac; correctness tests may also run under Rosetta here.

---

## 10. Isolation from production and behaviour guarantees

* Nothing in the running app imports the module; the prototype is invisible to users.
* No setting, cache file, schema, spec file or bridge source is modified in the prototype.
* The first integration (a later, separately approved step) is flag-gated, default off, shadow-compare first: run both engines on next-up lookups and log deltas without using the Rust result.

---

## 11. Path to integration (after the prototype passes)

1. Shadow mode in the app: compute with Rust in the existing isolated helper slot; log deltas; keep libmpv's value.
2. Setting `analysis_engine` (default libmpv) visible in Settings > Search/Library with a "Rust (experimental)" choice.
3. Turbo scan uses the Rust batch API; full scan rehearsal on the Intel Mac with the cache backed up first (AGENTS rule 7 applies to any real-cache run).
4. After two shows without regressions, flip the default; keep libmpv as the fallback for `Unsupported` inputs.

---

## 12. Acceptance criteria for the prototype

1. Builds and passes all tests on aarch64 and x86_64, macOS minimum 12.3 verified.
2. Integrated loudness within +-0.1 LU of libmpv for >= 99% of the oracle corpus, with each outlier explained (decoder difference, gating, clipping); sample peak within +-0.1 dB.
3. Silence boundaries within +-50 ms of the existing parser on >= 99% of the corpus.
4. Corrupt corpus: zero panics/hangs; error categories correct.
5. Benchmarks meet the targets in Section 8 on both Macs, or the shortfall is documented and a go/no-go is taken.
6. Python call works from the app's Python 3.14 venvs on both architectures; GIL released during batch analysis.
7. No change to any existing file other than adding the new `rust/` tree and `tools/rust_analysis_compare.py`.

If acceptance 2 or 5 fails, the correct outcome is to stop and keep the libmpv analysis; that is a valid result of the prototype.

---

## 13. Open questions before implementation

1. Approval to install the Rust toolchain on this Mac (rustup, user-level, ~1 GB) and to add the `rust/` tree. [Required]
2. Is it acceptable to copy a read-only snapshot of the venue Mac's `loudness.json` (paths and numbers only, no audio) here to enlarge the oracle corpus?
3. PyO3 vs CLI: choose after a one-hour packaging spike with PyInstaller 6.x on Python 3.14 (abi3 support and `maturin` behaviour under the Rosetta venv are unverified).
4. Deflate64: needed for any archives still in the library? (All 397 known Deflate64 ZIPs were repacked on 2026-08-30 [REPORTED], but newly added files could reintroduce them.)
5. Does the operator want Stage 1 at all before Stage 0 data exists? Stage 0 (instrumentation, no Rust) is cheaper and may reorder the roadmap.
