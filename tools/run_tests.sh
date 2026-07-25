#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv-test/bin/python" ]]; then
    PYTHON="$ROOT/.venv-test/bin/python"
else
    PYTHON="$ROOT/.venv-universal/bin/python"
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "Missing test environment: $PYTHON" >&2
    exit 1
fi

FRAMEWORK_VERSION_DIR="${SINGWS_PYTHON_FRAMEWORK_VERSION_DIR:-/private/tmp/python314-expanded/Python_Framework.pkg/Payload/Versions/3.14}"
FRAMEWORK_SHIM=""
SYSTEM_FRAMEWORK="/Library/Frameworks/Python.framework/Versions/3.14/Python"

if [[ ! -f "$SYSTEM_FRAMEWORK" && -f "$FRAMEWORK_VERSION_DIR/Python" ]]; then
    FRAMEWORK_SHIM="$(mktemp -d "${TMPDIR:-/tmp}/singws-python-framework.XXXXXX")"
    mkdir -p "$FRAMEWORK_SHIM/Python.framework/Versions"
    ln -s "$FRAMEWORK_VERSION_DIR" "$FRAMEWORK_SHIM/Python.framework/Versions/3.14"
    trap 'rm -rf "$FRAMEWORK_SHIM"' EXIT
    export DYLD_FRAMEWORK_PATH="$FRAMEWORK_SHIM${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
elif ! "$PYTHON" --version >/dev/null 2>&1; then
    if [[ ! -f "$FRAMEWORK_VERSION_DIR/Python" ]]; then
        echo "Python 3.14 framework is unavailable." >&2
        echo "Set SINGWS_PYTHON_FRAMEWORK_VERSION_DIR to its Versions/3.14 directory." >&2
        exit 1
    fi
    echo "Python test environment cannot start: $PYTHON" >&2
    exit 1
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${TMPDIR:-/tmp}/singws-test-pycache}"

if (($#)); then
    exec "$PYTHON" -m pytest "$@"
fi

# The extracted recovery framework cannot initialize Qt's macOS/offscreen
# platform plugin, so keep the default smoke suite headless. GUI tests can
# still be requested explicitly with a properly installed Python framework.
exec "$PYTHON" -m pytest -q \
    test_recent_regressions.py \
    test_performance_safety.py \
    -k "not rotation_level_meter_runs_only_during_active_playback"
