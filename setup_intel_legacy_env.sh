#!/bin/bash
# Create the isolated macOS 12/13 Intel packaging environment.
set -euo pipefail

cd "$(dirname "$0")"
USER_PYTHON312="$HOME/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
SYSTEM_PYTHON312="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
PYTHON312="${PYTHON312:-$USER_PYTHON312}"
if [[ ! -x "$PYTHON312" && -x "$SYSTEM_PYTHON312" ]]; then
    PYTHON312="$SYSTEM_PYTHON312"
fi
if [[ "$PYTHON312" == "$USER_PYTHON312" ]]; then
    export DYLD_FRAMEWORK_PATH="$HOME/Library/Frameworks${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
fi
VENV=".venv-intel-legacy"

[[ "$(uname -m)" == "x86_64" ]] || { echo "Run this on an Intel Mac."; exit 1; }
[[ -x "$PYTHON312" ]] || {
    echo "Python 3.12 universal2 is required: $PYTHON312"
    echo "Install Python 3.12.10 from python.org, then run this script again."
    exit 1
}

if [[ ! -f vendor/pybind11/include/pybind11/pybind11.h \
      || ! -f vendor/signalsmith-stretch/signalsmith-stretch.h \
      || ! -d vendor/signalsmith-linear/include ]]; then
    git submodule update --init --recursive -- \
        vendor/pybind11 vendor/signalsmith-stretch vendor/signalsmith-linear
fi

"$PYTHON312" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
LEGACY_WHEELHOUSE=".legacy-intel-wheelhouse"
mkdir -p "$LEGACY_WHEELHOUSE"
"$VENV/bin/python" -m pip download \
    --only-binary=:all: --platform macosx_12_0_x86_64 \
    --dest "$LEGACY_WHEELHOUSE" \
    PyQt6==6.8.1 PyQt6-Qt6==6.8.1 \
    numpy==2.5.1 scipy==1.18.0 \
    pyinstaller==6.21.0 pyinstaller-hooks-contrib==2026.6 \
    mutagen==1.48.1 pillow==12.1.1 psutil==7.2.2 \
    requests==2.34.2 certifi==2026.7.22 urllib3==2.7.0 \
    qrcode==8.2 dmgbuild==1.6.7 \
    pyobjc-core==12.2.1 pyobjc-framework-Cocoa==12.2.1 \
    pyobjc-framework-CoreLocation==12.2.1
"$VENV/bin/python" -m pip install --no-index --find-links "$LEGACY_WHEELHOUSE" \
    PyQt6==6.8.1 PyQt6-Qt6==6.8.1 \
    numpy==2.5.1 scipy==1.18.0 \
    pyinstaller==6.21.0 pyinstaller-hooks-contrib==2026.6 \
    mutagen==1.48.1 pillow==12.1.1 psutil==7.2.2 \
    requests==2.34.2 certifi==2026.7.22 urllib3==2.7.0 \
    qrcode==8.2 dmgbuild==1.6.7 \
    pyobjc-core==12.2.1 pyobjc-framework-Cocoa==12.2.1 \
    pyobjc-framework-CoreLocation==12.2.1

"$VENV/bin/python" -c "import PyQt6, numpy, scipy, requests, mutagen, PIL"
MACOSX_DEPLOYMENT_TARGET=12.0 "$VENV/bin/python" \
    native/setup_signalsmith_audio.py build_ext --inplace
[[ -e signalsmith_audio_native.cpython-312-darwin.so ]] || {
    echo "Python 3.12 SignalSmith extension was not produced"
    exit 1
}
echo "Legacy environment ready: $VENV"
