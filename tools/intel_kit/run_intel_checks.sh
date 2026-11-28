#!/bin/bash
# One command: checks the installed SingWS on THIS Mac (read-only, silent, changes nothing). Run it right after installing an update, again after opening SingWS once
# (so the log and the saved settings can be checked), and after a show.
#   bash run_intel_checks.sh                       # expects this Mac's architecture and whatever version is installed
#   bash run_intel_checks.sh 1.0.4.5               # also fail unless that version is installed and was the last one launched
# The report is also saved on the Desktop. Send it (or a photo of the RESULT line) if anything says FAIL.
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/intel_selfcheck.py"; [ -f "$SCRIPT" ] || SCRIPT="$HERE/tools/intel_selfcheck.py"
[ -f "$SCRIPT" ] || { echo "intel_selfcheck.py is not next to this script"; exit 2; }
PY="$(command -v python3 || true)"; [ -n "$PY" ] || { echo "python3 is not installed (macOS offers to install the developer tools the first time you run python3)"; exit 2; }
ARGS=(); [ -n "$1" ] && ARGS+=(--expect-version "$1")
exec "$PY" "$SCRIPT" "${ARGS[@]}"
