#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer an interpreter backed by a real Python 3.14 install. Qt resolves its
# platform plugins relative to the interpreter, and a venv hanging off an
# extracted .pkg payload makes it find zero valid plugins -- every GUI test then
# aborts, intermittently and with correct-looking library paths.
PYTHON=""
for candidate in "$ROOT/.venv-test" "$ROOT/.venv-test-brew" "$ROOT/.venv-universal"; do
    if [[ -x "$candidate/bin/python" ]]; then
        PYTHON="$candidate/bin/python"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "Missing test environment: python3.14 -m venv .venv-test" >&2
    exit 1
fi

# Only fall back to a framework shim when the chosen interpreter cannot start on
# its own (i.e. it needs the deleted system framework). Prefer the durable copy
# under ~/.singws-python314 over an extracted payload in /tmp, which macOS purges.
FRAMEWORK_VERSION_DIR="${SINGWS_PYTHON_FRAMEWORK_VERSION_DIR:-}"
if [[ -z "$FRAMEWORK_VERSION_DIR" ]]; then
    for candidate in \
        "$HOME/.singws-python314/Python.framework/Versions/3.14" \
        "/private/tmp/python314-expanded/Python_Framework.pkg/Payload/Versions/3.14"; do
        if [[ -f "$candidate/Python" ]]; then
            FRAMEWORK_VERSION_DIR="$candidate"
            break
        fi
    done
fi
if ! "$PYTHON" --version >/dev/null 2>&1; then
    if [[ ! -f "$FRAMEWORK_VERSION_DIR/Python" ]]; then
        echo "Python 3.14 framework is unavailable and $PYTHON cannot start." >&2
        echo "Set SINGWS_PYTHON_FRAMEWORK_VERSION_DIR to its Versions/3.14 directory." >&2
        exit 1
    fi
    FRAMEWORK_SHIM="$(mktemp -d "${TMPDIR:-/tmp}/singws-python-framework.XXXXXX")"
    mkdir -p "$FRAMEWORK_SHIM/Python.framework/Versions"
    ln -s "$FRAMEWORK_VERSION_DIR" "$FRAMEWORK_SHIM/Python.framework/Versions/3.14"
    trap 'rm -rf "$FRAMEWORK_SHIM"' EXIT
    export DYLD_FRAMEWORK_PATH="$FRAMEWORK_SHIM${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
    if ! "$PYTHON" --version >/dev/null 2>&1; then
        echo "Python test environment cannot start: $PYTHON" >&2
        exit 1
    fi
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${TMPDIR:-/tmp}/singws-test-pycache}"

# Never touch the operator's live data. Importing the main module opens
# ~/SingWS/logs/singws_<today>.log, and anything reaching save_settings() or
# save_data() rewrites the real settings.json and queue. On 2026-08-09 test runs
# put thousands of lines into the log being used to diagnose a live fault.
# A per-run scratch home keeps the suite hermetic.
if [[ -z "${SINGWS_HOME:-}" ]]; then
    SINGWS_HOME="$(mktemp -d "${TMPDIR:-/tmp}/singws-test-home.XXXXXX")"
    export SINGWS_HOME
    trap 'rm -rf "$SINGWS_HOME"' EXIT
fi
echo "test data root: $SINGWS_HOME"

PYTEST_EXTRA=()
if ! "$PYTHON" -c 'from PyQt6.QtWidgets import QApplication; app = QApplication([])' \
        >/dev/null 2>&1; then
    echo "Warning: Qt platform integration is unavailable; running the full non-GUI suite." >&2
    for gui_test in \
        test_bg_video_lyrics.py \
        test_karaoke_output_dsp.py \
        test_model_view_qa.py \
        test_performance_safety.py \
        test_rotation_render_thread.py \
        test_show_screen_vfx.py \
        test_ticker_and_qr.py; do
        PYTEST_EXTRA+=("--ignore=$gui_test")
    done
fi

if (($#)); then
    exec "$PYTHON" -m pytest "${PYTEST_EXTRA[@]}" "$@"
fi

# Full desktop suite, GUI included. test_bass_init_once.py is a standalone
# script with no pytest cases, so it is excluded rather than reported as a
# collection failure; run it directly if you need it.
#
# vendor/pybind11 ships upstream's own suite, which needs a compiled pybind11_tests module that
# is never built here. Both aborted collection outright, so this runner — and
# therefore release.sh, which gates on it — could not finish at all.
exec "$PYTHON" -m pytest -q \
    "${PYTEST_EXTRA[@]}" \
    --ignore=test_bass_init_once.py \
    --ignore=vendor
