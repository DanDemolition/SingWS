# Stage E: Rust log redaction and last-show package (`singws-logpack`) - findings

Status: built offline and wired as a log-only shadow (setting `logpack_shadow`, default True). The app never uses the Rust answer; the Python
package is the one built and uploaded. Nothing here sends anything or touches the recipient, server or upload code.

## What exists

- `rust/crates/singws-logpack` - pure library (`sanitize`, `splitlines_keepends`, `last_show_slice`, `select_files`, `build`) and the CLI
  `singws-logpack` (`redact`, `build` from JSON on stdin, `build-dir DIR` that reads a folder the way the app does).
- `logpack_shadow.py` - the helper runner and comparison. Hook: end of `_build_last_show_package` in `0.2.18.1.py`, after the ZIP is renamed
  into place. It snapshots the folder listing on the calling thread, then a daemon thread runs the helper at `nice 10` and compares text, window,
  files used and the redacted extra files. The Python numbers come from the very strings the app wrote into the ZIP.
- Log line `[LOGPACK-SHADOW] MISMATCH <what>: app=... rust=...` only on a difference, `skipped: <reason>` only when the helper fails. A missing
  helper binary is silent. **Log text is never written** to these lines (it can hold secrets): a mismatch carries lengths, a short hash and the
  first differing offset. No existing log line changed.
- `rust/build_logpack.sh` builds `native/singws_logpack/<arch>/singws-logpack`; both installer specs bundle it when present and list the module.
- `tools/logpack_parity.py`, `tools/mutate_logpack.py`, `test_logpack_shadow.py`, 15 crate unit tests.

## Python behaviours copied exactly (found by the parity tool, not assumed)

- `\s` is `str.isspace` (includes U+001C-U+001F, U+0085), not Rust's whitespace; `\d` is Unicode Nd (76 digit runs, table from Python 3.14 /
  Unicode 16); `(?i)` also equates U+0130/U+0131 with i, U+212A with k and U+017F with s. Checked for every code point.
- `Path.read_text` turns `\r\n` and lone `\r` into `\n`; `str.splitlines` splits on `\v \f \x1c-\x1e \x85    ` too.
- File names: `Path.suffix` differs between Python versions for names that start with dots (`"..log"` is `.log` on 3.13, empty on 3.14). The crate
  follows 3.14 (the dev venv). Such a file name does not occur in practice.
- Selection: `days` is clamped to 1-30, mtime `>=` cutoff, then ordered by (mtime, name), as the app does before merging.

## Redactor gap found and closed (operator approved, 2026-10-31)

`_sanitize_log_text` did not redact a **quoted** value (`password = 'hunter2'`, `{"api_key": "abc"}`): the value class `[^\s,'"&]+` cannot start with a
quote. A read-only, count-only scan of the real logs (1.2M lines) found 0 such lines, so nothing has leaked in practice. Python and Rust were widened
together: a new first pattern redacts everything up to the closing quote (or the end of the line if it never closes), and the keyword may now be
followed by a quote (`"token": 123`). Unquoted behaviour is unchanged. `pin`, `key` and `secret` still match inside longer words (harmless over-redaction).

## Verified

See the report that accompanied the commit for the exact numbers (parity cases, mutants killed/documented, tests). Not verified: the helper
inside a frozen installer build (no installer was built; `rust/build_logpack.sh` was not run for both architectures here), a real bug-report
build in the running app, and real-world timing of the shadow thread on the Intel Mac. The shadow reads the helper from the bundle root,
`Frameworks/`, or `native/singws_logpack/<arch>/` (override `SINGWS_LOGPACK_BIN`).
