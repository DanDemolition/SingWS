#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer a self-contained interpreter. .venv-test-brew is built from a real
# Homebrew python@3.14 install, so Qt can resolve its platform plugins and the
# GUI suites run. The older environments hang off an extracted framework
# payload, where Qt finds zero valid platform plugins and every GUI test aborts.
PYTHON=""
for candidate in "$ROOT/.venv-test-brew" "$ROOT/.venv-test" "$ROOT/.venv-universal"; do
    if [[ -x "$candidate/bin/python" ]]; then
        PYTHON="$candidate/bin/python"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "Missing test environment: create .venv-test-brew from python@3.14" >&2
    exit 1
fi

# Only fall back to the extracted-payload shim when the chosen interpreter
# cannot start on its own (i.e. it needs the deleted system framework).
FRAMEWORK_VERSION_DIR="${SINGWS_PYTHON_FRAMEWORK_VERSION_DIR:-/private/tmp/python314-expanded/Python_Framework.pkg/Payload/Versions/3.14}"
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

if (($#)); then
    exec "$PYTHON" -m pytest "$@"
fi

# Full desktop suite, GUI included. test_bass_init_once.py is a standalone
# script with no pytest cases, so it is excluded rather than reported as a
# collection failure; run it directly if you need it.
exec "$PYTHON" -m pytest -q \
    --ignore=test_bass_init_once.py
