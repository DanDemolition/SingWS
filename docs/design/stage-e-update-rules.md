# Stage E: update decision rules in Rust (shadow)

`rust/crates/singws-update` (+ JSON CLI `singws-update`, `--batch` for tools) reproduces the pure part of the app's update check: `_version_key`,
`_is_newer_version`, `_preferred_update_asset`, `GitHubUpdateWorker._release_info` and `_manifest_info` (0.2.18.1.py). Inputs are the manifest/release dict, the
current version, `sys.platform` and `platform.machine()` as strings. Nothing fetches, downloads or replaces files; the Python network and download code is untouched.

* Parity oracle: `tools/update_parity.py` runs Python's REAL functions (platform/machine patched per case) and the CLI over 9,659 cases: adversarial versions (leading
  v/V, 3 to 7 parts, junk, empty, Unicode digits, 4300/4301-digit runs), every present/missing combination of `mac_arm64`/`mac_x86_64`/`mac_universal`, url/filename/sha
  variations, releases with assets in different orders and names, machines arm64/aarch64/x86_64/amd64/i386/i686/uppercase/other/empty, platforms darwin/linux/win32, the real
  `docs/release.json`, plus seeded random cases. Result: IDENTICAL. `tools/mutate_update.py`: 44 mutants, 43 killed, 1 documented equivalent (an empty version).
* Python quirks reproduced: the version key splits on every non-ASCII-digit character, numbers are unbounded, a digit run over 4300 characters fails `int()` and counts as 0,
  the key is padded to 4 parts and cut to 6, `str.strip()` uses Python's whitespace set (including \x1c-\x1f, NEL, ideographic space). Not modelled: non-string JSON values
  (`str(5)`), non-dict assets (Python raises); the corpus feeds strings, null or missing.
* Shadow (`update_shadow.py`, setting `update_shadow`, default True): the worker calls `_update_shadow(kind, payload, info)` right after it computed `info` (a copy; the
  emitted dict is never touched); a daemon thread runs the helper at nice 10. Log lines (`UPDATE-SHADOW`): `enabled available=0/1` at launch, `first check clean (...)`, and
  `MISMATCH <kind> at <path>: app=<summary> rust=<summary>` (once per distinct difference, at most 10). Bundling: `rust/build_update.sh`, both specs, `.gitignore`, both
  `build_singws_mac_*.sh`; QUIET SHADOWS row in `tools/shadow_report.py` ("needs an update check").
* Not verified: a real update check on a Mac (no GitHub or server was contacted by any test).
