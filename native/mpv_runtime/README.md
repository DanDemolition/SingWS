# Pinned SingWS media runtime

SingWS plays audible audio and renders video through an in-process libmpv
instance. The application does not use a separate `mpv` executable. These
sources therefore build and pin the libmpv dylib that the native SingWS bridge
loads.

The runtime is mpv 0.41.0 with the macOS 27 CoreAudio workaround reviewed in
[Anki PR 5363](https://github.com/ankitects/anki/pull/5363), for the failure
tracked in [mpv issue 18384](https://github.com/mpv-player/mpv/issues/18384).
The patch removes the failing `kAudioOutputUnitProperty_ChannelMap` property
write. It does not add an audio delay or change SingWS synchronization logic.

## Build

The complete version and checksum lock is `versions.env`. Build each slice on
an Apple Silicon Mac with Apple's command-line tools plus Meson, Ninja and
pkg-config available:

```bash
bash native/mpv_runtime/build_runtime.sh arm64
bash native/mpv_runtime/build_runtime.sh x86_64
```

The script builds FFmpeg, libplacebo, libass, FreeType, FriBidi, HarfBuzz and
Rubber Band as static position-independent libraries, then links them into one
`singws_libmpv.2.dylib`. Its only dynamic dependencies are Apple system
libraries. This avoids inheriting Homebrew's changing dependency graph or
deployment targets.

Both builds set `MACOSX_DEPLOYMENT_TARGET`, C, C++, Objective-C, Objective-C++
and Swift targets to macOS 12.3. `tools/verify_mpv_runtime.py` rejects the
wrong architecture, a newer deployment target, a non-system dylib, a missing
feature, the wrong mpv version, or an unpatched CoreAudio failure string.
The app builders run this verification again before PyInstaller.

Artifacts are written to:

```text
native/mpv_runtime/artifacts/arm64/Frameworks/
native/mpv_runtime/artifacts/x86_64/Frameworks/
```

The architecture-specific app builder compiles the native bridge into the same
directory. The PyInstaller specs use these directories directly, so installing
or upgrading Homebrew mpv cannot silently change a SingWS build.

## Required playback checks

After building and signing, test on the actual target systems:

1. On macOS 27, play an MP4 and confirm the mpv log selects CoreAudio without
   OSStatus -50 or an AVFoundation fallback.
2. Repeat stop/start on an MP4 with known beep/flash markers and check that
   sound, video and lyrics remain aligned.
3. Play MP3+G and ZIP CDG songs, including pause/resume and seek, and check
   lyrics timing.
4. With **Play stereo audio as mono** disabled in macOS, play a left/right
   channel-identification file and confirm each channel reaches the correct
   speaker. This guards the stereo-routing behavior affected by the workaround.
5. Repeat the basic MP4 and CDG checks on an Intel Mac running macOS 12.3.

Automated build checks can prove architecture, dependency closure, loadability,
signing and declared deployment targets. They cannot prove speaker routing,
perceptual synchronization, or real macOS 12.3 execution; those require the
listed hardware tests.

## Source and licensing

`LICENSES.md` records the bundled projects, versions, source locations and
licenses. Preserve that file, the patch files, `versions.env`, and the
corresponding complete source archives with every distributed build. mpv and
Rubber Band make the combined libmpv runtime GPL-covered; distribution must
include the GPL notices and a compliant offer or copy of the complete
corresponding source, including these patches and build instructions.
