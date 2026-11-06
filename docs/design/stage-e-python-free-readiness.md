# Python-free readiness: the hardware gates, as one operator test

The Python-free road (docs/design/stage-e-no-python-gap.md) is blocked on things only real hardware can answer: the audience **TV**, **KaraFun**'s UI, and the **Intel Mac**. This kit runs
those questions as ONE command on the Mac in question and produces a report the show checklist reads, so a completed show/rehearsal can close the gates.

## Run it (on the Mac under test)
1. Copy `SingWS Shell.app` (the Rust shell, `rust/package_shell.sh OUT`, universal) to `/Applications`, and `check_python_free.sh` + `after_show.sh` from `tools/intel_kit/` next to the other scripts of the
   Intel folder. Connect the TV, start KaraFun, quit SingWS.
2. `./check_python_free.sh /path/to/any-karaoke-song.zip` (an MP3+G zip; without it only the machine and KaraFun checks run).
3. Answer the one question that appears on screen ("is the karaoke picture on the TV?"). Results: `~/SingWS/readiness/<time>/readiness.txt|json` + screenshots of every display.
4. After the show, `after_show.sh` adds the newest run to the zip; `python3 tools/show_checklist.py <zip>` prints the `py-free:` rows and a **Python-free road verdict**.

It changes nothing in SingWS (settings, queue, history), makes no sound (the music engine runs headless) and never plays or searches in KaraFun: it only READS KaraFun's window and UI element count.

## Why it launches the shell app itself (`open -W -n -a`)
macOS Accessibility and Automation grants belong to the APP that asks. The Python SingWS has its own; the Rust shell is a different app (`com.singws.shell`) and needs its own. Started from Terminal the probe would
borrow Terminal's grants and prove nothing. Launched as an app, every child process (selfcheck, play demo, display demo) shares the shell's identity.

## What it checks (shell `--readiness-run DIR [--song ZIP]`, crate `singws-shell/src/readiness.rs`)
| Row | Meaning | Gate |
|---|---|---|
| machine, native process | Intel or Apple Silicon, **Rosetta or native** (warn when translated: not Intel evidence), graphics + Metal, attached displays | Intel Mac |
| TV connected | two or more displays attached | TV |
| helpers | every bundled helper contains this Mac's architecture | Intel Mac |
| Accessibility (shell) | the shell itself is a trusted Accessibility client | KaraFun |
| System Events / KaraFun process / **KaraFun UI (shell)** | the shell can read KaraFun's front window and its UI elements (>= 20 elements) through NSAppleScript, read-only | KaraFun |
| host spike (selfcheck) | one Rust process hosting a Qt window + the Rust video bridge + the Rust music engine, with the song's files | Intel Mac / TV |
| song through Rust picture + voice | a whole song through the Rust video bridge and the Rust karaoke voice, clock slaved by the Rust protocol port (the `--play-demo-full` report) | Intel Mac |
| **audience display (TV)** | the Rust display window opened where the app's audience window would (`--display-place`), screenshots of every display, and the operator's yes/no | TV |

The verdict row is **PASS only when** native process, Accessibility, KaraFun UI, host spike, song and TV all pass **on an x86_64 Mac**; the same on Apple Silicon says "Intel still unproven".

## What this does NOT prove (the honest limits)
* KaraFun automation itself (searching, starting, the end monitor, the capture) is not ported to Rust yet; this proves the shell can REACH KaraFun's UI, the precondition for porting it.
* The TV answer is the operator's eyes, backed by screenshots; nothing here measures smoothness or latency on the TV.
* A debug build has no bundled `libmpv`: the song steps then fail with "libmpv not found" (that is the probe working). Use the packaged app; `SINGWS_LIBMPV` can point a debug build at a runtime.
* Verified on the Apple Silicon dev Mac without a TV and without KaraFun running; the TV, KaraFun UI and Intel rows have never been run on their hardware.
