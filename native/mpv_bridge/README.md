# SingWS native mpv bridge (IINA-derived stack)

`libsingws_mpv_bridge.dylib` is the in-process libmpv core behind
`mpv_playback_iina.py`: one libmpv instance renders each frame once into a
shared texture presented by two native NSViews (output + preview). No
mpv-owned windows, no `wid`, no follower synchronization.

## Building

The IINA-derived media libraries are **not** in this repo (see Licensing).
Point the build at the directory containing `singws_libmpv.2.dylib`:

```bash
./build_bridge.sh --arch x86_64 --frameworks /path/to/Frameworks
```

Deployment target is pinned to **macOS 12.0**. Verify afterwards:

```bash
tools/verify_macos_min_version.py native/mpv_bridge/libsingws_mpv_bridge.dylib \
    --arch x86_64 --maximum 12.0
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
  composes both into `af`, key first.
* Both are re-applied on `MPV_EVENT_FILE_LOADED`, so the chain survives song
  changes.

An earlier revision had `setSemitones:` overwrite `af` outright, silently
wiping the EQ and master bus on every key change.
`test_mpv_audio_filters.IinaBackendContractTests` guards against its return.

## Licensing

IINA and mpv are **GPL-licensed**. Distributing a product built on this stack
carries GPL source and notice obligations. Resolve this before shipping.
