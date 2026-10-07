# SingWS native mpv bridge

`libsingws_mpv_bridge.dylib` is the in-process libmpv core behind
`mpv_playback_iina.py`: one libmpv instance renders each frame once into a
shared texture presented by two native NSViews (output + preview). No
mpv-owned windows, no `wid`, no follower synchronization.

## Where the runtime comes from

`native/mpv_runtime/build_runtime.sh` builds the pinned, patched mpv 0.41.0
runtime for arm64 or x86_64. See `native/mpv_runtime/README.md` for sources,
licenses, checksums and the macOS 12.3 compatibility policy. The old extracted
IINA dependency tree is no longer used by the app builders.

## Building

Point the build at the directory containing `singws_libmpv.2.dylib`:

```bash
./build_bridge.sh --arch x86_64 \
  --frameworks ../mpv_runtime/artifacts/x86_64/Frameworks
```

Deployment target is pinned to **macOS 12.3**. Verify afterwards:

```bash
tools/verify_macos_min_version.py native/mpv_bridge/libsingws_mpv_bridge.dylib \
    --arch x86_64 --maximum 12.3
```

At runtime the dylib resolves libmpv through `@loader_path` and
`@loader_path/Frameworks`, so it must sit beside the bundled libraries (or
beside a `Frameworks/` directory containing them).

## Audio filter ownership

mpv has a **single** `af` property, and both the key change (rubberband) and
the SingWS DSP chain (normalize → EQ → master bus) need it. Neither writes it
directly:

* Python sends the DSP half via `singws_bridge_set_dsp_chain`, built by
  `mpv_audio_filters.build_af_chain(semitones=0, ...)`.
* The bridge stores it alongside `_desiredSemitones` and `applyAudioFilters`
  composes both into `af`, with DSP before the speed-changing rubberband stage
  to avoid libmpv's documented desynchronization warning.
* Both are re-applied on `MPV_EVENT_FILE_LOADED`, so the chain survives song
  changes.

An earlier revision had `setSemitones:` overwrite `af` outright, silently
wiping the EQ and master bus on every key change.
`test_mpv_audio_filters.IinaBackendContractTests` guards against its return.

## Licensing

The combined runtime is GPL-covered. See `native/mpv_runtime/LICENSES.md` and
ship the required license texts and corresponding source with releases.
