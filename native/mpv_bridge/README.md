# SingWS native mpv bridge (IINA-derived stack)

`libsingws_mpv_bridge.dylib` is the in-process libmpv core behind
`mpv_playback_iina.py`: one libmpv instance renders each frame once into a
shared texture presented by two native NSViews (output + preview). No
mpv-owned windows, no `wid`, no follower synchronization.

## Where the frameworks come from

The IINA-derived media libraries are **not** in this repo (see Licensing) and
never will be: 71 prebuilt GPL dylibs, ~62 MB, x86_64 only. They are extracted
from an IINA.app bundle and renamed with the `singws_` prefix.

The tree expects them at **`native_dual_view/Frameworks/`** (repo root), which
is gitignored — so a fresh checkout does not have them and cannot build Intel
until they are put back by hand. Copy the directory in, or point
`SINGWS_MPV_FRAMEWORKS` at wherever you keep it; every consumer honours that
variable first:

| Consumer | Default if `SINGWS_MPV_FRAMEWORKS` is unset |
| --- | --- |
| `build_singws_mac_intel.sh` | `$(pwd)/native_dual_view/Frameworks` |
| `SingWS-x86_64.spec` | `<repo root>/native_dual_view/Frameworks` |
| `build_bridge.sh` | `$HOME/Downloads/native_dual_view/Frameworks` |

Note the third row disagrees with the other two — the bridge builder still
defaults to the download location the stack first arrived in. Pass
`--frameworks` explicitly, or export `SINGWS_MPV_FRAMEWORKS`, rather than
relying on any of these defaults.

This is the same arrangement as `vendor/mpv-iina-Frameworks/`: a local artifact
the build depends on, deliberately kept out of git.

## Building

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
