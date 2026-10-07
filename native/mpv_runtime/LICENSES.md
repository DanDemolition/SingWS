# Bundled media runtime source and licenses

The exact download checksums and the libplacebo commit are in `versions.env`.
The build combines these components into `singws_libmpv.2.dylib`.

| Component | Pinned version | License | Source |
| --- | --- | --- | --- |
| mpv | 0.41.0 + SingWS CoreAudio patch | GPL-2.0-or-later | <https://github.com/mpv-player/mpv> |
| FFmpeg | 8.1.2, GPL configuration inherited by the combined work | LGPL-2.1-or-later / GPL-2.0-or-later by configuration | <https://ffmpeg.org/releases/> |
| libplacebo | 7.360.1 (`cee9b076…`) | LGPL-2.1-or-later | <https://code.videolan.org/videolan/libplacebo> |
| libass | 0.17.5 | ISC | <https://github.com/libass/libass> |
| FreeType | 2.14.3 | FreeType License or GPL-2.0-only | <https://github.com/freetype/freetype> |
| FriBidi | 1.0.16 | LGPL-2.1-or-later | <https://github.com/fribidi/fribidi> |
| HarfBuzz | 14.2.1 | Old MIT | <https://github.com/harfbuzz/harfbuzz> |
| Rubber Band | 4.0.0 + Apple Clang portability patch | GPL-2.0-or-later (unless separately commercially licensed) | <https://breakfastquay.com/rubberband/> |

Apple frameworks are dynamically linked system components and are not copied
into the runtime. The build disables X11, Vulkan, Lua/JavaScript, optical-disc
libraries, libarchive, shaderc/glslang, LCMS and other optional third-party
dependencies that SingWS playback does not use.

Before public distribution, stage the unmodified source archives, the recursive
libplacebo checkout at its pinned commit, all license texts, the two patches,
and `build_runtime.sh` together as the corresponding-source package. This file
is an engineering inventory, not a substitute for the upstream license texts.
