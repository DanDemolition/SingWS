#!/bin/bash
# Builds the GStreamer soundtouch plugin (pitch + bpmdetect elements) against
# the Homebrew GStreamer stack. Homebrew's monolithic `gstreamer` formula ships
# without soundtouch, but the karaoke transport needs the `pitch` element for
# realtime key changes. The official GStreamer.framework (used by the
# x86_64/universal builds) already includes this plugin; this build covers the
# dev environment and the arm64 (Homebrew-based) bundle.
#
# Sources are gst-plugins-bad 1.28.3 ext/soundtouch, fetched verbatim from the
# GStreamer monorepo (LGPL). Requires: brew install gstreamer sound-touch
set -euo pipefail
cd "$(dirname "$0")"

GST="$(brew --prefix gstreamer)"
ST="$(brew --prefix sound-touch)"
GLIB="$(brew --prefix glib)"

clang++ -shared -fPIC -std=c++14 -O2 \
  -DPACKAGE='"gst-plugins-bad"' -DVERSION='"1.28.3"' \
  -DGST_PACKAGE_NAME='"GStreamer Bad Plug-ins"' \
  -DGST_PACKAGE_ORIGIN='"https://gstreamer.freedesktop.org"' \
  -DGST_LICENSE='"LGPL"' -DPACKAGE_VERSION='"1.28.3"' \
  -DHAVE_SOUNDTOUCH_1_4=1 \
  -I"$GST/include/gstreamer-1.0" \
  -I"$GLIB/include/glib-2.0" -I"$GLIB/lib/glib-2.0/include" \
  -I"$ST/include" -I"$ST/include/soundtouch" \
  -x c++ gstpitch.cc gstbpmdetect.cc plugin.c \
  -L"$GST/lib" -L"$GLIB/lib" -L"$ST/lib" \
  -lgstreamer-1.0 -lgstaudio-1.0 -lgstbase-1.0 \
  -lglib-2.0 -lgobject-2.0 -lSoundTouch \
  -o libgstsoundtouch.dylib

echo "Built: $(pwd)/libgstsoundtouch.dylib"
GST_PLUGIN_PATH="$(pwd)" "$GST/bin/gst-inspect-1.0" pitch | head -5
