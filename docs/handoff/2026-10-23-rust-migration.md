# Rust migration handoff — 2026-10-23

## Current state

The repository is still a Python/PyQt application, but its native playback, background-audio, analysis, and initial song
lifecycle decision paths are Rust-backed. The first production lifecycle decision—KaraFun automatic-end detection—is now
enabled by default through Rust. Python still computes the same answer and automatically wins any disagreement.

Rollback is immediate: set `transport_drive` to `false` in the profile settings. Do not remove the Python oracle until all
remaining transport decisions have independently passed the same comparison and package gates.

## Completed work

- `dd1f077`: performance fixes and one-time transport-shadow enable migration.
- `bba7fb5`: ordered no-Python/Rust migration roadmap.
- `c762835`: expanded transport-shadow scenarios.
- `6f3d4db`: dual-architecture A0 verification.
- `a706399`: deterministic local three-show acceptance harness and operator-approved substitute gate.
- `99af5d3`: A2 guarded Rust KaraFun-end drive decision; default off, Python fallback on disagreement.
- A3 (this handoff checkpoint): `transport_drive` defaults on for only KaraFun automatic-end; Python comparison/fallback
  retained.

The A3 source and package evidence:

- 26 transport-shadow/report tests passed.
- 152 recent regression tests passed.
- Three show-shaped logs/five lifecycle sessions passed with zero disagreements and complete coverage.
- Real CDG+MP3 and MP4 transport integration passed start, key/tempo, seek, pause/resume, and stop. Worst clock errors:
  77 ms CDG and 21 ms MP4.
- Signed arm64 and x86_64 DMGs passed architecture, minimum-macOS, signature, image verification, isolated-profile launch,
  and clean automatic shutdown.

Artifacts are local and intentionally not released:

- `SingWS-1.0.1.4-arm64-installer.dmg`
  (`baec6d84e2436c46a8ddf0fc06e4317e54d2ebd410368afddd86ebaa60d0379d`)
- `SingWS-1.0.1.4-x86_64-installer.dmg`
  (`e6526df8a3b00a7a1cb101ba020ec82f364ddf742bb8f794e7ad8a81bd8b440a`)

## Continue in this order

1. Confirm the A3 checkpoint and worktree before editing; never absorb unrelated changes.
2. Extend Rust drive ownership one decision at a time: native media end, watchdog expiry, then stop/skip. For each:
   preserve the Python oracle, log disagreements, and make Python win them.
3. After every decision, run transport tests, all recent regressions, `tools/local_transport_acceptance.py`, real
   `test_karaoke_transport_integration`, and both signed package smoke tests.
4. Only after all transport decisions pass should A4 remove duplicated Python lifecycle rules. Retain a separately tested
   emergency fallback; do not turn `transport_drive: false` into a dead or misleading switch.
5. Stage B: implement the queue/rotation replay harness first, then a pure `singws-queue` Rust state machine with invariant
   tests (host deletion never resurrects, IDs never change, oldest duplicate wins), shadow it, then drive host actions first.
6. Stage C: port the pure background-transition planner and prove parity on real playlist traces.
7. Stage D: move settings/history/stats stores and HTTP/websocket clients behind compatibility adapters.
8. Stage E: replace the PyQt host shell panel by panel with a Rust-owned Qt Quick/QML shell. Python/PyInstaller can only be
   removed from packaging after the shell and all required adapters pass both architecture gates.

## Useful commands

```bash
PYTHONPYCACHEPREFIX=/tmp/singws-pyc .venv/bin/python -m unittest test_transport_shadow test_transport_shadow_report
PYTHONPYCACHEPREFIX=/tmp/singws-pyc .venv/bin/python -m unittest test_recent_regressions
PYTHONPYCACHEPREFIX=/tmp/singws-pyc .venv/bin/python tools/local_transport_acceptance.py
```

For Intel packaging on this Apple Silicon development Mac:

```bash
SINGWS_NATIVE_PYTHON="$PWD/.venv-build-arm64/bin/python" \
SINGWS_BUILD_PYTHON="$PWD/.venv-build-intel-rosetta/bin/python" \
./build_singws_mac_intel.sh
```

The complete rationale and gates live in `RUST_APP_LOGIC_PLAN.md`; detailed evidence is in
`docs/verification/2026-10-23-rust-transport-shadow-a0.md`.
