#!/bin/bash
# Reproducibly build SingWS's small, self-contained libmpv runtime.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
. ./versions.env

ARCH="${1:-}"
case "$ARCH" in arm64|x86_64) ;; *) echo "usage: $0 arm64|x86_64" >&2; exit 2 ;; esac

WORK="${SINGWS_RUNTIME_WORK:-/tmp/singws-mpv-runtime-$ARCH}"
DOWNLOADS="$WORK/downloads"
SOURCES="$WORK/sources"
BUILDS="$WORK/build"
PREFIX="$WORK/prefix"
ARTIFACTS="$ROOT/native/mpv_runtime/artifacts/$ARCH/Frameworks"
JOBS="${SINGWS_BUILD_JOBS:-$(sysctl -n hw.logicalcpu)}"
export MACOSX_DEPLOYMENT_TARGET
export CFLAGS="-arch $ARCH -mmacosx-version-min=$MACOS_DEPLOYMENT_TARGET -O2"
export CXXFLAGS="$CFLAGS"
export OBJCFLAGS="$CFLAGS"
export OBJCXXFLAGS="$CFLAGS"
export LDFLAGS="-arch $ARCH -mmacosx-version-min=$MACOS_DEPLOYMENT_TARGET -lc++"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export CLANG_MODULE_CACHE_PATH="$WORK/clang-module-cache"
export SWIFT_MODULE_CACHE_PATH="$WORK/swift-module-cache"

mkdir -p "$DOWNLOADS" "$SOURCES" "$BUILDS" "$PREFIX" "$ARTIFACTS"

fetch() {
    local url="$1" sha="$2" out="$3"
    if [[ ! -f "$out" ]]; then curl -L --fail --retry 3 -o "$out" "$url"; fi
    [[ "$(shasum -a 256 "$out" | awk '{print $1}')" == "$sha" ]] || {
        echo "checksum mismatch: $out" >&2; exit 1;
    }
}

unpack() {
    local archive="$1" directory="$2"
    [[ -d "$directory" ]] || tar -xf "$archive" -C "$SOURCES"
}

fetch "$MPV_URL" "$MPV_SHA256" "$DOWNLOADS/mpv.tar.gz"
fetch "$FFMPEG_URL" "$FFMPEG_SHA256" "$DOWNLOADS/ffmpeg.tar.xz"
fetch "$LIBASS_URL" "$LIBASS_SHA256" "$DOWNLOADS/libass.tar.xz"
fetch "$FREETYPE_URL" "$FREETYPE_SHA256" "$DOWNLOADS/freetype.tar.gz"
fetch "$FRIBIDI_URL" "$FRIBIDI_SHA256" "$DOWNLOADS/fribidi.tar.xz"
fetch "$HARFBUZZ_URL" "$HARFBUZZ_SHA256" "$DOWNLOADS/harfbuzz.tar.xz"
fetch "$RUBBERBAND_URL" "$RUBBERBAND_SHA256" "$DOWNLOADS/rubberband.tar.bz2"

unpack "$DOWNLOADS/mpv.tar.gz" "$SOURCES/mpv-$MPV_VERSION"
unpack "$DOWNLOADS/ffmpeg.tar.xz" "$SOURCES/ffmpeg-$FFMPEG_VERSION"
unpack "$DOWNLOADS/libass.tar.xz" "$SOURCES/libass-$LIBASS_VERSION"
unpack "$DOWNLOADS/freetype.tar.gz" "$SOURCES/freetype-VER-$(tr . - <<<"$FREETYPE_VERSION")"
unpack "$DOWNLOADS/fribidi.tar.xz" "$SOURCES/fribidi-$FRIBIDI_VERSION"
unpack "$DOWNLOADS/harfbuzz.tar.xz" "$SOURCES/harfbuzz-$HARFBUZZ_VERSION"
unpack "$DOWNLOADS/rubberband.tar.bz2" "$SOURCES/rubberband-$RUBBERBAND_VERSION"

if [[ ! -d "$SOURCES/libplacebo/.git" ]]; then
    git clone --recursive "$LIBPLACEBO_REPOSITORY" "$SOURCES/libplacebo"
fi
git -C "$SOURCES/libplacebo" fetch --tags
git -C "$SOURCES/libplacebo" checkout --detach "$LIBPLACEBO_COMMIT"
git -C "$SOURCES/libplacebo" submodule update --init --recursive
[[ "$(git -C "$SOURCES/libplacebo" rev-parse HEAD)" == "$LIBPLACEBO_COMMIT" ]]

if grep -Fq 'unable to set the input channel layout on the audio unit' \
    "$SOURCES/mpv-$MPV_VERSION/audio/out/ao_coreaudio.c"; then
    patch -p1 -d "$SOURCES/mpv-$MPV_VERSION" < patches/mpv-macos27-coreaudio.patch
fi
if ! grep -Fq '#include <cstddef>' \
    "$SOURCES/rubberband-$RUBBERBAND_VERSION/src/common/mathmisc.h"; then
    patch -p1 -d "$SOURCES/rubberband-$RUBBERBAND_VERSION" < patches/rubberband-clang-size-t.patch
fi

CROSS=()
if [[ "$ARCH" == x86_64 ]]; then
    CROSS_FILE="$WORK/x86_64.ini"
    cat > "$CROSS_FILE" <<EOF
[binaries]
c = ['clang']
cpp = ['clang++']
objc = ['clang']
objcpp = ['clang++']
ar = ['ar']
strip = ['strip']
pkg-config = ['pkg-config']
exe_wrapper = ['arch', '-x86_64']
[host_machine]
system = 'darwin'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
[properties]
needs_exe_wrapper = true
EOF
    CROSS=(--cross-file "$CROSS_FILE")
fi

meson_build() {
    local name="$1" source="$2"; shift 2
    meson setup --wipe ${CROSS[@]+"${CROSS[@]}"} --prefix "$PREFIX" --default-library static \
        "$BUILDS/$name" "$source" "$@"
    meson compile -C "$BUILDS/$name" -j "$JOBS"
    meson install -C "$BUILDS/$name"
}

meson_build freetype "$SOURCES/freetype-VER-$(tr . - <<<"$FREETYPE_VERSION")" \
    -Dharfbuzz=disabled -Dpng=disabled -Dbrotli=disabled -Dbzip2=enabled -Dzlib=enabled -Dtests=disabled
meson_build fribidi "$SOURCES/fribidi-$FRIBIDI_VERSION" -Ddocs=false -Dtests=false -Dbin=false
meson_build harfbuzz "$SOURCES/harfbuzz-$HARFBUZZ_VERSION" \
    -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dicu=disabled \
    -Dtests=disabled -Ddocs=disabled -Dutilities=disabled
meson_build libass "$SOURCES/libass-$LIBASS_VERSION" \
    -Dtest=disabled -Dcompare=disabled -Dfontconfig=disabled -Ddirectwrite=disabled -Dlibunibreak=disabled
meson_build rubberband "$SOURCES/rubberband-$RUBBERBAND_VERSION" \
    -Dfft=vdsp -Dresampler=builtin -Dcmdline=disabled -Dtests=disabled -Djni=disabled \
    -Dladspa=disabled -Dlv2=disabled -Dvamp=disabled
meson_build libplacebo "$SOURCES/libplacebo" \
    -Dvulkan=disabled -Dopengl=enabled -Dgl-proc-addr=enabled -Dglslang=disabled \
    -Dshaderc=disabled -Dlcms=disabled -Ddovi=disabled -Ddemos=false -Dtests=false \
    -Dbench=false -Dfuzz=false -Dunwind=disabled -Dxxhash=disabled

if [[ ! -f "$PREFIX/lib/libavcodec.a" ]]; then
    rm -rf "$BUILDS/ffmpeg"
    mkdir -p "$BUILDS/ffmpeg"
    (
    cd "$BUILDS/ffmpeg"
    "$SOURCES/ffmpeg-$FFMPEG_VERSION/configure" \
        --prefix="$PREFIX" --arch="$ARCH" --target-os=darwin --cc=clang --cxx=clang++ \
        --enable-static --disable-shared --enable-pic --disable-programs --disable-doc \
        --disable-debug --disable-autodetect --enable-iconv --enable-zlib --enable-bzlib \
        --enable-videotoolbox --enable-audiotoolbox --enable-securetransport \
        --extra-cflags="$CFLAGS" --extra-cxxflags="$CXXFLAGS" --extra-ldflags="$LDFLAGS"
    make -j "$JOBS"
    make install
    )
fi

meson setup --wipe ${CROSS[@]+"${CROSS[@]}"} --prefix "$PREFIX" --default-library shared \
    "$BUILDS/mpv" "$SOURCES/mpv-$MPV_VERSION" \
    -Dcplayer=false -Dlibmpv=true -Dbuild-date=false -Dtests=false \
    -Djavascript=disabled -Dlua=disabled -Dlibarchive=disabled -Dlibavdevice=disabled \
    -Dlibbluray=disabled -Dcdda=disabled -Ddvdnav=disabled -Drubberband=enabled \
    -Duchardet=disabled -Dvapoursynth=disabled -Dzimg=disabled -Djpeg=disabled -Dlcms2=disabled \
    -Dcoreaudio=enabled -Davfoundation=enabled -Dx11=disabled -Dx11-clipboard=disabled \
    -Dwayland=disabled -Ddrm=disabled -Dgbm=disabled -Degl=disabled -Dcaca=disabled -Dsixel=disabled \
    -Dcocoa=enabled -Dgl=enabled -Dplain-gl=enabled -Dgl-cocoa=enabled -Dvulkan=disabled \
    -Dshaderc=disabled -Dspirv-cross=disabled -Dvideotoolbox-gl=enabled -Dvideotoolbox-pl=disabled \
    -Dswift-build=enabled -Dswift-flags="-target $ARCH-apple-macosx$MACOS_DEPLOYMENT_TARGET" \
    -Dmacos-cocoa-cb=enabled -Dmacos-media-player=disabled -Dmacos-touchbar=disabled \
    -Dhtml-build=disabled -Dmanpage-build=disabled
meson compile -C "$BUILDS/mpv" -j "$JOBS"
meson install -C "$BUILDS/mpv"

cp "$PREFIX/lib/libmpv.2.dylib" "$ARTIFACTS/singws_libmpv.2.dylib"
install_name_tool -id '@rpath/singws_libmpv.2.dylib' "$ARTIFACTS/singws_libmpv.2.dylib"
cp "$PREFIX/include/mpv/"*.h "$ROOT/native/mpv_bridge/include/mpv/"
python3 "$ROOT/tools/verify_mpv_runtime.py" "$ARTIFACTS/singws_libmpv.2.dylib" \
    --arch "$ARCH" --maximum "$MACOS_DEPLOYMENT_TARGET"
shasum -a 256 "$ARTIFACTS/singws_libmpv.2.dylib" > "$ARTIFACTS/SHA256SUMS"
echo "Built $ARTIFACTS/singws_libmpv.2.dylib"
