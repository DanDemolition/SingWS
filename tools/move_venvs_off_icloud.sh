#!/bin/bash
# Move the build virtualenvs out of the iCloud-synced ~/Documents and leave a symlink at the old path.
#
# Why: on macOS 27 the iCloud file provider keeps re-applying the `hidden` file flag to files inside the dot-named venvs under
# ~/Documents. Qt's directory listing skips hidden files, so PyQt6 then finds no platform plugins ("Could not find the Qt
# platform plugin 'offscreen'") and every GUI test and the real-transport integration test fail within seconds of
# `chflags -R nohidden`. The flag is a property of the files, so the files have to live outside iCloud.
#
# Why a symlink and not a rebuild: a venv bakes absolute paths into pyvenv.cfg and every script shebang. Moving the real
# directory and keeping the old path as a symlink leaves all of them valid, so no build script or spec needs to change.
#
#   tools/move_venvs_off_icloud.sh [--dry-run] [--restore] [venv-dir-name ...]
#
# Default venvs: .venv-build-arm64 .venv-build-intel-rosetta (what build_singws_mac_*.sh and the tests use; `.venv` is a
# symlink to .venv-build-arm64). Destination root: $SINGWS_VENV_HOME, default ~/.venvs/singws.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0; RESTORE=0; NAMES=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --restore) RESTORE=1 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) NAMES+=("$arg") ;;
    esac
done
[[ ${#NAMES[@]} -gt 0 ]] || NAMES=(.venv-build-arm64 .venv-build-intel-rosetta)
DEST_ROOT="${SINGWS_VENV_HOME:-$HOME/.venvs/singws}"
ROOT="$(pwd -P)"

run() { if [[ $DRY -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

busy() { # is anything running out of this venv?
    pgrep -f "$ROOT/$1/" >/dev/null 2>&1 || pgrep -f "$DEST_ROOT/$1/" >/dev/null 2>&1
}

verify() { # name
    local py="$ROOT/$1/bin/python" runner=()
    [[ "$1" == *intel* ]] && runner=(arch -x86_64)
    # macOS bash 3.2 treats an empty array as unbound under `set -u`, hence the ${runner[@]+...} form.
    ${runner[@]+"${runner[@]}"} "$py" -c 'import sys; print("  python ok:", sys.prefix)'
    if ${runner[@]+"${runner[@]}"} "$py" -c 'import PyQt6.QtCore' 2>/dev/null; then
        QT_QPA_PLATFORM=offscreen ${runner[@]+"${runner[@]}"} "$py" -c \
            'from PyQt6.QtWidgets import QApplication; QApplication([]); print("  QApplication ok")'
    else
        echo "  (PyQt6 not installed in $1; skipped the Qt check)"
    fi
}

for name in "${NAMES[@]}"; do
    path="$ROOT/$name"; dest="$DEST_ROOT/$name"
    echo "== $name"
    if [[ $RESTORE -eq 1 ]]; then
        if [[ ! -L "$path" ]]; then echo "  not a symlink; nothing to restore"; continue; fi
        [[ -d "$dest" ]] || { echo "  missing $dest" >&2; exit 1; }
        busy "$name" && { echo "  something is running from this venv; stop it first" >&2; exit 1; }
        run rm "$path"; run mv "$dest" "$path"
        [[ $DRY -eq 1 ]] || verify "$name"
        continue
    fi
    if [[ -L "$path" ]]; then echo "  already a symlink -> $(readlink "$path"); skipping"; continue; fi
    if [[ ! -d "$path" ]]; then echo "  no such directory; skipping"; continue; fi
    [[ ! -e "$dest" ]] || { echo "  destination exists: $dest" >&2; exit 1; }
    busy "$name" && { echo "  something is running from this venv (a build or test?); stop it first" >&2; exit 1; }
    run mkdir -p "$DEST_ROOT"
    run mv "$path" "$dest"
    run ln -s "$dest" "$path"
    run chflags -R nohidden "$dest"
    [[ $DRY -eq 1 ]] || verify "$name"
done
echo "done"
