#!/usr/bin/env python3
"""Mutation test of the Rust KaraFun DRIVER (rust/crates/singws-karafun-svc: src/driver.rs, src/runtime.rs, and the Start split in src/host.rs).

Mutants (operators, comparisons, booleans, integer/float constants; string text is never touched) are made in a SCRATCH COPY of the crate
(~/.cache/singws-karafun-driver-mut), never in the repository. A mutant is killed when the build breaks, the crate's own tests fail, the driver parity
against the real Python host+service differs (tools/karafun_driver_parity.py) or the process tests (test_karafun_driver.py) fail. A survivor must carry a
note in NOTES: `equivalent: why` or `GAP: what the corpus lacks`.
  SINGWS_HOME=$(mktemp -d) .venv/bin/python tools/mutate_karafun_driver.py [--file driver.rs|runtime.rs|host.rs] [--only 3,7] [--cases N]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="kf_drv_mut_home_"))
import mutate_karafun_svc as ms  # noqa: E402  (generate: the same mutant generator)

CRATE = ROOT / "rust/crates/singws-karafun-svc"
WORK = Path(os.path.expanduser("~/.cache/singws-karafun-driver-mut"))
TARGET = os.path.expanduser("~/.cache/singws-rust-target-kfdrv-mut")
BIN = os.path.join(TARGET, "debug", "singws-karafun-svc")   # a debug build: the process tests that run the real Keychain reader need its SINGWS_SECURITY_BIN stand-in
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, SINGWS_KF_SVC_BIN=BIN, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.path.expanduser("~/.cache/singws-pytest"), PATH=os.path.expanduser("~/.cargo/bin") + ":" + os.environ["PATH"])
MANIFEST = str(WORK / "crate" / "Cargo.toml")

# "<file>|<snippet[:70]>|<old>-><new>" -> "equivalent: why" | "GAP: what is missing"
NOTES: dict = {
    'driver.rs|self.returns.unwrap_or(false)|false->true': 'equivalent: the song machine always answers `returns` after StartBranch, so the default is never used',
    'driver.rs|if t.0 <= now && pos.map_or(true, |p: usize| t.0 < self.timers[p].0) {|<-><=': 'equivalent: two timers due at the same instant (watchdog and hold) give the same app-visible effects in either order',
    'driver.rs|let ms = arg(1).as_f64().unwrap_or(0.0);|0.0->1.0': 'equivalent: `hold_timer` always carries its milliseconds; the default is for a malformed effect only',
    'runtime.rs|self.generation += 1;|1->2': 'equivalent: the generation counter is only compared for equality with the number its own reader captured; 1 or 2 per spawn makes no difference',
    'runtime.rs|self.generation += 1; // its reader\'s "gone" is then not an error|1->2': "equivalent: the generation counter is only compared for equality; any change makes the old reader's `gone` stale",
    'runtime.rs|let stamp = std::time::SystemTime::now().duration_since(std::time::UNI|0->1': 'equivalent: the clock is never before the Unix epoch, so the 0 default never applies (and the stamp only names a backup folder)',
    'runtime.rs|let io = Real { display_path, profile: arg("--profile"), email: arg("-|0->1': "equivalent: the first spawn's generation is 1 either way relative to itself; the initial 0 is only a starting point for the counter",
    'runtime.rs|let wait = d.next_deadline().map(|t| Duration::from_secs_f64((t - now(|0.0->1.0': "equivalent: the clamp's lower bound only matters for a timer already due, and every loop pass runs the due timers before waiting",
    'runtime.rs|let wait = d.next_deadline().map(|t| Duration::from_secs_f64((t - now(|3600.0->3601.0': 'equivalent: upper clamp 3600 s vs 3601 s: a timer that far out does not exist',
    'runtime.rs|let wait = d.next_deadline().map(|t| Duration::from_secs_f64((t - now(|3600->3601': 'equivalent: idle wait with no timers 3600 s vs 3601 s: only the interval between idle wake-ups',
    'runtime.rs|Err(RecvTimeoutError::Disconnected) => return 0,|0->1': 'equivalent: the channel cannot disconnect while the loop holds its own sender; the exit code there is unreachable',
    # host.rs survivors of the 2026-11-18 run (mutants 1-144 of 240; 145-240 were not run):
    'host.rs|Some(Value::Number(n)) => Ok(n.as_f64().unwrap_or(0.0)),|0.0->1.0': "equivalent: serde_json's `as_f64` on a Number is always Some (no arbitrary-precision feature), so the 0.0 default is unreachable",
    'host.rs|gens: 0,|0->1': 'equivalent: generation numbers are only compared with each other for equality; the starting value of the counter makes no difference',
    'host.rs|self.gens += 1;|1->2': 'equivalent: generation numbers are only compared with each other for equality; stepping by 2 instead of 1 makes no difference',
    'host.rs|let key_i = key.unwrap_or(0);|0->1': 'equivalent: the asked key is only read when no key was shown yet, and a start always sets the shown key first (key_current = 0)',
    'host.rs|self.key_current = Some(0);|0->1': 'GAP: the key a restarted player plays with is the shown key (0 after a start); no scenario restarts the player after a start with a non-zero asked key',
    'host.rs|self.tempo_current = Some(100);|100->101': 'GAP: the tempo a restarted player plays with is the shown tempo (100 after a start); no scenario restarts the player after a start with a non-100 asked tempo',
    'host.rs|o.push(json!(["returns", true]));|true->false': 'GAP: `start` answers true when the song already failed during the set-up; the driver tests have no scenario in which the set-up itself fails the song',
    'host.rs|let playing = m.get("playing").map(truthy).unwrap_or(false) || m.get("|false->true': 'GAP: a status without `playing` or `moving` must not look like a playing stray song; the driver routes statuses through the sign-in machine first and the tests have no no-song, no-`playing` scenario that reaches the host',
    'host.rs|Value::Bool(false) => String::new(),|false->true': "equivalent: the driver always hands the host the error TEXT as a string (non-strings are stringified first), so the host's non-string arms are reached only by the replay parity",
    'host.rs|Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),|==->!=': "equivalent: the driver always hands the host the error TEXT as a string (non-strings are stringified first), so the host's non-string arms are reached only by the replay parity",
    'host.rs|Value::Number(n) if n.as_f64() == Some(0.0) => String::new(),|0.0->1.0': "equivalent: the driver always hands the host the error TEXT as a string (non-strings are stringified first), so the host's non-string arms are reached only by the replay parity",
    'host.rs|a.hold_armed = false;|false->true': 'equivalent: the hold timer fires 25 ms after the hold ends, so by then the play is sent at once and the arm flag is not consulted again for that song',
    'host.rs|let ok = self.active(w) && self.asked.as_ref().map(|a| a.heard).unwrap|false->true': 'equivalent: `active` already requires an open song, so the `unwrap_or(false)` for no song is never evaluated',
    'host.rs|let ok = self.active(w) && self.asked.as_ref().map(|a| a.heard).unwrap|false->true': 'equivalent: `active` already requires an open song, so the `unwrap_or(false)` for no song is never evaluated',
    'host.rs|Some(p) if p != 0.0 => p,|0.0->1.0': "equivalent: the pause time is 0.0 only if the clock reads exactly 0.0, which the app's monotonic clock never does",
    'host.rs|_ => 0.0,|0.0->1.0': 'GAP: the hold left for a song whose play was already sent; no scenario makes the player ready again after the play went out while the song still counts as unsent',
    'host.rs|if left <= 0.02 {|<=-><': 'equivalent: the hold left equals exactly 0.02 s only by float coincidence; a timer 25 ms later always follows',
}

END_MARKERS = ("pub mod replay", "#[cfg(test)]", "#[cfg(target_os")


def generate(name, text):
    lines = text.split("\n")
    stop = next((i for i, l in enumerate(lines) if l.startswith(END_MARKERS)), len(lines))
    return ms.generate(name, "\n".join(lines[:stop]))


_CONST = re.compile(r"^(\s*pub const [A-Z_0-9]+: (?:f64|u64|i64|usize|u32|i32) = )(-?[0-9][0-9_]*(?:\.[0-9]+)?)(;.*)$")


def const_mutants(name, text):
    """`--consts`: the numeric `pub const` values (timer lengths, status counts), which `generate` skips: floats doubled and halved, integers +1 and -1."""
    out = []
    for i, line in enumerate(text.split("\n"), 1):
        m = _CONST.match(line)
        if not m:
            continue
        s, e = m.start(2), m.end(2)
        raw = m.group(2).replace("_", "")
        if "." in raw:
            v = float(raw)
            news = [f"{v * 2:.1f}", f"{v / 2:.2f}".rstrip("0").rstrip(".") + (".0" if float(f"{v / 2:.2f}") == int(v / 2) else "")]
        else:
            v = int(raw)
            news = [str(v + 1)] + ([str(v - 1)] if v > 0 else [])
        out += [(name, i, s, e, n, line.strip()) for n in news]
    return out


def sh(cmd, timeout=240, **kw):
    """A mutant that makes the program spin forever is caught by the timeout (returncode 124)."""
    # its own process group, so a timeout kills the whole tree: a mutant that spins forever lives in a CHILD of the check (the parity script starts the driver),
    # and killing only the direct child leaves that grandchild running with the output pipes open (the run then hangs in communicate() for hours)
    proc = subprocess.Popen(cmd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT, start_new_session=True, **kw)
    try:
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.communicate()
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def build():
    return sh(["cargo", "build", "--offline", "--quiet", "--manifest-path", MANIFEST]).returncode == 0


def verdict(cases, process_tests):
    if sh(["cargo", "test", "--offline", "--quiet", "--manifest-path", MANIFEST]).returncode != 0:
        return "the crate tests"
    if sh([sys.executable, "tools/karafun_driver_parity.py", "--cases", str(cases)]).returncode != 0:
        return "the driver parity"
    if process_tests and sh([sys.executable, "-m", "pytest", "-x", "-q", "test_karafun_driver.py"]).returncode != 0:
        return "the process tests"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--only")
    ap.add_argument("--consts", action="store_true", help="mutate the numeric `pub const` values of lib.rs and host.rs instead of the operators")
    ap.add_argument("--cases", type=int, default=1500)
    a = ap.parse_args()
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(CRATE, WORK / "crate", copy_function=shutil.copyfile)
    (WORK / "crate" / "Cargo.toml").write_text((CRATE / "Cargo.toml").read_text() + "\n[workspace]\n")
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "crate" / "Cargo.lock")
    assert build() and verdict(a.cases, True) == "", "the unmutated crate must pass every check first"
    files = [a.file] if a.file else (["lib.rs", "host.rs"] if a.consts else ["driver.rs", "runtime.rs"])
    orig = {f: (CRATE / "src" / f).read_text() for f in files}
    muts = [m for f in files for m in (const_mutants(f, orig[f]) if a.consts else generate(f, orig[f]))]
    print(f"mutants: {len(muts)}", flush=True)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    killed = documented = 0
    undocumented = []
    for n, (fname, ln, s, e, new, snippet) in enumerate(muts, 1):
        if only and n not in only:
            continue
        lines = orig[fname].split("\n")
        line = lines[ln - 1]
        lines[ln - 1] = line[:s] + new + line[e:]
        (WORK / "crate" / "src" / fname).write_text("\n".join(lines))
        key = f"{fname}|{snippet[:70]}|{line[s:e]}->{new}"
        try:
            if not build():
                killed += 1
                print(f"#{n} {fname}:{ln} does not compile", flush=True)
                continue
            by = verdict(a.cases, process_tests=True)
            if by:
                killed += 1
                print(f"#{n} {fname}:{ln} {line[s:e]!r}->{new!r}: killed by {by}", flush=True)
            elif key in NOTES:
                documented += 1
                print(f"#{n} {fname}:{ln} SURVIVED {NOTES[key][:90]}", flush=True)
            else:
                undocumented.append(n)
                print(f"#{n} {fname}:{ln} {line[s:e]!r}->{new!r}: SURVIVED (UNDOCUMENTED) key={key!r}", flush=True)
        finally:
            (WORK / "crate" / "src" / fname).write_text(orig[fname])
    print(f"killed {killed}, documented survivors {documented}, UNDOCUMENTED {len(undocumented)} {undocumented}", flush=True)
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
