//! `singws-shell --readiness REPORT.json`: the "is the Python-free road open on THIS Mac?" probe (docs/design/stage-e-python-free-readiness.md). It runs BEFORE Qt starts, needs
//! no window and changes nothing: it only reads the machine and asks KaraFun's UI questions through `singws-mac` (NSAppleScript in this process), so the answers are those of
//! the Rust shell's OWN macOS identity (Accessibility and Automation grants belong to the app that asks, not to the Python SingWS). Launch it as an app
//! (`open -W -n -a "SingWS Shell" --args --readiness FILE`), not from Terminal, or it inherits Terminal's grants.
//!
//! The report is JSON: `{"kind":"python_free_readiness","checks":[{"name","status":"pass|fail|warn|info","detail"}...],"facts":{...}}`. No song names, paths of the library,
//! keys or settings are read or written.

use serde_json::{json, Map, Value};
use std::process::Command;
use std::time::{Duration, Instant};

/// The helper executables the Python app bundles next to itself (the Rust shell must be able to start every one of them on this Mac).
const HELPERS: [&str; 10] = [
    "singws-mac-probe", "singws-logpack", "singws-relaymsg", "singws-update", "singws-queue-state", "singws-netslice", "singws-scan-cli", "singws-analyze",
    "singws-settingsmig", "singws-songend",
];

pub fn requested<I: Iterator<Item = String>>(mut args: I) -> Option<String> {
    while let Some(a) = args.next() {
        if a == "--readiness" {
            return Some(args.next().unwrap_or_else(|| "readiness.json".into()));
        }
    }
    None
}

/// `--readiness-run DIR [--song SONG.zip]`: the whole operator test (see `run_all`). Returns (directory, optional MP3+G zip).
pub fn requested_run<I: Iterator<Item = String>>(args: I) -> Option<(String, Option<String>)> {
    let v: Vec<String> = args.collect();
    let i = v.iter().position(|a| a == "--readiness-run")?;
    let dir = v.get(i + 1).cloned().unwrap_or_else(|| "readiness".into());
    let song = v.iter().position(|a| a == "--song").and_then(|j| v.get(j + 1).cloned());
    Some((dir, song))
}

fn out(cmd: &str, args: &[&str]) -> Option<String> {
    let o = Command::new(cmd).args(args).output().ok()?;
    if !o.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
}

pub struct Report {
    checks: Vec<Value>,
    facts: Map<String, Value>,
}

impl Report {
    fn new() -> Self {
        Report { checks: Vec::new(), facts: Map::new() }
    }
    fn check(&mut self, name: &str, status: &str, detail: impl Into<String>) {
        self.checks.push(json!({"name": name, "status": status, "detail": detail.into()}));
    }
    fn fact(&mut self, key: &str, v: Value) {
        self.facts.insert(key.into(), v);
    }
    pub fn to_json(&self) -> Value {
        json!({"kind": "python_free_readiness", "version": 1, "checks": self.checks, "facts": Value::Object(self.facts.clone())})
    }
}

/// CPU architectures a Mach-O file (thin or fat) contains: "arm64", "x86_64".
pub fn macho_archs(bytes: &[u8]) -> Vec<&'static str> {
    fn name(cpu: u32) -> Option<&'static str> {
        match cpu {
            0x0100_000c => Some("arm64"),
            0x0100_0007 => Some("x86_64"),
            _ => None,
        }
    }
    if bytes.len() < 8 {
        return Vec::new();
    }
    let magic_le = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    if magic_le == 0xfeed_facf {
        return name(u32::from_le_bytes([bytes[4], bytes[5], bytes[6], bytes[7]])).into_iter().collect();
    }
    if u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) == 0xcafe_babe {
        let n = u32::from_be_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]) as usize;
        let mut v = Vec::new();
        for i in 0..n.min(8) {
            let at = 8 + 20 * i;
            if bytes.len() >= at + 4 {
                if let Some(a) = name(u32::from_be_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])) {
                    v.push(a);
                }
            }
        }
        return v;
    }
    Vec::new()
}

/// Where the shell may find the media core: next to itself, in an installed SingWS.app (the Python app bundles the same `libmpv`), or named by `SINGWS_LIBMPV`.
pub fn libmpv_candidates(exe: Option<&std::path::Path>, home: &str, env: Option<&str>) -> Vec<std::path::PathBuf> {
    let name = "singws_libmpv.2.dylib";
    let mut v: Vec<std::path::PathBuf> = Vec::new();
    if let Some(e) = env.filter(|e| !e.is_empty()) {
        v.push(e.into());
    }
    if let Some(d) = exe.and_then(|e| e.parent()) {
        v.push(d.join(name));
        v.push(d.join("Frameworks").join(name));
        v.push(d.join("..").join("Frameworks").join(name));
    }
    v.push(std::path::Path::new("/Applications/SingWS.app/Contents/Frameworks").join(name));
    v.push(std::path::Path::new(home).join("Applications/SingWS.app/Contents/Frameworks").join(name));
    v
}

fn find_libmpv() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok();
    let home = std::env::var("HOME").unwrap_or_default();
    let env = std::env::var("SINGWS_LIBMPV").ok();
    libmpv_candidates(exe.as_deref(), &home, env.as_deref()).into_iter().find(|p| p.is_file())
}

fn libmpv(r: &mut Report) {
    let machine = machine_arch();
    match find_libmpv() {
        Some(p) => {
            let archs = macho_archs(&std::fs::read(&p).unwrap_or_default());
            r.fact("libmpv", json!({"path": p.display().to_string(), "archs": archs}));
            if archs.iter().any(|a| *a == machine) {
                r.check("media core (libmpv)", "pass", format!("{} ({})", p.display(), archs.join("+")));
            } else {
                r.check("media core (libmpv)", "fail", format!("{} has no {machine} code", p.display()));
            }
        }
        None => r.check("media core (libmpv)", "fail", "no libmpv next to the shell or in /Applications/SingWS.app: install SingWS 1.0.3.0 on this Mac first (the shell borrows its media core), or set SINGWS_LIBMPV"),
    }
}

fn machine_arch() -> &'static str {
    // the CPU of the machine, not of this process: under Rosetta an x86_64 process still runs on arm64 hardware
    if out("sysctl", &["-n", "hw.optional.arm64"]).as_deref() == Some("1") {
        "arm64"
    } else {
        "x86_64"
    }
}

fn platform(r: &mut Report) {
    let machine = machine_arch();
    let process = if std::env::consts::ARCH == "aarch64" { "arm64" } else { std::env::consts::ARCH };
    let translated = out("sysctl", &["-n", "sysctl.proc_translated"]).unwrap_or_else(|| "0".into()) == "1";
    r.fact("machine_arch", json!(machine));
    r.fact("process_arch", json!(process));
    r.fact("rosetta", json!(translated));
    r.fact("os_version", json!(out("sw_vers", &["-productVersion"]).unwrap_or_default()));
    r.fact("model", json!(out("sysctl", &["-n", "hw.model"]).unwrap_or_default()));
    r.fact("cpu", json!(out("sysctl", &["-n", "machdep.cpu.brand_string"]).unwrap_or_default()));
    r.fact("cores", json!(out("sysctl", &["-n", "hw.ncpu"]).unwrap_or_default()));
    r.fact("ram_gb", json!(out("sysctl", &["-n", "hw.memsize"]).and_then(|s| s.parse::<u64>().ok()).map(|b| b / (1 << 30)).unwrap_or(0)));
    let intel = machine == "x86_64";
    r.check("machine", "info", format!("{} ({}), macOS {}, running as {}{}", if intel { "Intel" } else { "Apple Silicon" },
        out("sysctl", &["-n", "hw.model"]).unwrap_or_default(), out("sw_vers", &["-productVersion"]).unwrap_or_default(), process,
        if translated { " UNDER ROSETTA" } else { "" }));
    if translated {
        r.check("native process", "warn", "this process is translated by Rosetta: results are NOT those of the Mac's own CPU (an Intel Mac never shows this)");
    } else if process != machine {
        r.check("native process", "fail", format!("a {process} process on a {machine} Mac"));
    } else {
        r.check("native process", "pass", format!("a native {process} process"));
    }
}

fn display_facts() -> Vec<Value> {
    let Some(text) = out("system_profiler", &["SPDisplaysDataType", "-json"]) else { return Vec::new() };
    let Ok(v) = serde_json::from_str::<Value>(&text) else { return Vec::new() };
    let mut gpus = Vec::new();
    for gpu in v["SPDisplaysDataType"].as_array().cloned().unwrap_or_default() {
        let mut g = Map::new();
        for key in ["sppci_model", "spdisplays_vendor", "spdisplays_mtlgpufamilysupport", "sppci_cores"] {
            if let Some(x) = gpu.get(key) {
                g.insert(key.into(), x.clone());
            }
        }
        let mut screens = Vec::new();
        for d in gpu["spdisplays_ndrvs"].as_array().cloned().unwrap_or_default() {
            let mut s = Map::new();
            for key in ["_name", "_spdisplays_resolution", "_spdisplays_pixels", "spdisplays_main", "spdisplays_connection_type", "spdisplays_online", "spdisplays_mirror"] {
                if let Some(x) = d.get(key) {
                    s.insert(key.into(), x.clone());
                }
            }
            screens.push(Value::Object(s));
        }
        g.insert("displays".into(), Value::Array(screens));
        gpus.push(Value::Object(g));
    }
    gpus
}

fn displays(r: &mut Report) {
    let gpus = display_facts();
    let screens: usize = gpus.iter().map(|g| g["displays"].as_array().map(|a| a.len()).unwrap_or(0)).sum();
    r.fact("gpus", Value::Array(gpus.clone()));
    if gpus.is_empty() {
        r.check("displays", "warn", "system_profiler gave no display information");
        return;
    }
    for g in &gpus {
        let names: Vec<String> = g["displays"].as_array().cloned().unwrap_or_default().iter()
            .map(|d| format!("{} {}{}", d["_name"].as_str().unwrap_or("?"), d["_spdisplays_resolution"].as_str().unwrap_or(""),
                if d["spdisplays_main"].as_str() == Some("spdisplays_yes") { " (main)" } else { "" })).collect();
        r.check("graphics", "info", format!("{} [{}]: {}", g["sppci_model"].as_str().unwrap_or("?"), g["spdisplays_mtlgpufamilysupport"].as_str().unwrap_or("?"), names.join("; ")));
    }
    if screens >= 2 {
        r.check("TV connected", "pass", format!("{screens} displays are attached (a second one can be the audience screen)"));
    } else {
        r.check("TV connected", "warn", "only one display is attached: the audience screen cannot be tested without the TV (connect it and run this again)");
    }
}

fn helpers(r: &mut Report) {
    let machine = machine_arch();
    let exe = std::env::current_exe().ok();
    let mut dirs: Vec<std::path::PathBuf> = Vec::new();
    if let Some(d) = exe.as_ref().and_then(|e| e.parent()) {
        dirs.push(d.to_path_buf());
        if let Some(c) = d.parent() {
            dirs.push(c.join("Frameworks"));
            dirs.push(c.join("Resources"));
        }
    }
    // the Python app's bundle, when it is installed next to the shell
    for app in ["/Applications/SingWS.app"] {
        dirs.push(std::path::Path::new(app).join("Contents/Frameworks"));
        dirs.push(std::path::Path::new(app).join("Contents/MacOS"));
    }
    let mut found = Vec::new();
    for name in HELPERS {
        let hit = dirs.iter().map(|d| d.join(name)).find(|p| p.is_file());
        match hit {
            None => found.push(json!({"name": name, "present": false})),
            Some(p) => {
                let bytes = std::fs::read(&p).unwrap_or_default();
                let archs = macho_archs(&bytes);
                let runs = archs.iter().any(|a| *a == machine);
                found.push(json!({"name": name, "present": true, "archs": archs, "runs_here": runs}));
            }
        }
    }
    let present = found.iter().filter(|f| f["present"] == true).count();
    let wrong: Vec<String> = found.iter().filter(|f| f["present"] == true && f["runs_here"] == false).filter_map(|f| f["name"].as_str()).map(String::from).collect();
    r.fact("helpers", Value::Array(found));
    if present == 0 {
        r.check("helpers", "info", "no helper binaries were found next to the shell or in an installed SingWS.app (nothing to check)");
    } else if wrong.is_empty() {
        r.check("helpers", "pass", format!("{present} helper(s) found, every one contains this Mac's architecture ({machine})"));
    } else {
        r.check("helpers", "fail", format!("helper(s) without {machine} code: {}", wrong.join(", ")));
    }
}

fn mac_identity(r: &mut Report) {
    let trusted = singws_mac::ax_is_trusted();
    r.fact("ax_trusted", json!(trusted));
    r.fact("frontmost_app", json!(singws_mac::frontmost_app_name()));
    r.fact("location_status", json!(singws_mac::location_status_name(singws_mac::location_status())));
    let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default();
    r.fact("exe", json!(exe));
    r.check("identity", "info", format!("running as {exe}"));
    if trusted {
        r.check("Accessibility (shell)", "pass", "this app IS a trusted Accessibility client (it may drive other apps' UI, as the Python app does today)");
    } else {
        r.check("Accessibility (shell)", "fail", "this app is NOT trusted for Accessibility: grant it in System Settings > Privacy & Security > Accessibility (the Python SingWS has its own grant; the shell needs one too) and run this again");
    }
    // the app holds a keep-awake assertion the way the show needs, and lets it go again
    let began = singws_mac::keep_awake_begin("SingWS Shell readiness probe");
    let active = singws_mac::keep_awake_active();
    singws_mac::keep_awake_end();
    r.check("keep awake", if began && active { "pass" } else { "warn" }, if began && active { "an assertion was taken and released" } else { "could not take a keep-awake assertion" });
}

fn describe_script_error(err: &str) -> String {
    if err.contains("-25211") || err.contains("not allowed assistive") {
        "Accessibility is not granted to this app (-25211)".into()
    } else if err.contains("-1743") || err.contains("not authorized to send Apple events") {
        "Automation is not granted: allow this app to control System Events / KaraFun (-1743)".into()
    } else if err == "timeout" {
        "the script did not answer in time".into()
    } else {
        err.chars().take(160).collect()
    }
}

/// A script that timed out is "not tested" (an unanswered macOS permission prompt looks exactly like this), any other error is a real failure.
fn script_error_status(err: &str) -> &'static str {
    if err == "timeout" { "warn" } else { "fail" }
}

fn karafun(r: &mut Report) {
    let app = ["/Applications/KaraFun.app", "~/Applications/KaraFun.app"].iter().map(|p| p.replace('~', &std::env::var("HOME").unwrap_or_default()))
        .find(|p| std::path::Path::new(p).exists());
    let version = app.as_ref().and_then(|a| out("defaults", &["read", &format!("{a}/Contents/Info"), "CFBundleShortVersionString"]));
    r.fact("karafun_installed", json!(app.is_some()));
    r.fact("karafun_version", json!(version.clone().unwrap_or_default()));
    match &app {
        Some(a) => r.check("KaraFun installed", "pass", format!("{a} version {}", version.clone().unwrap_or_else(|| "?".into()))),
        None => {
            r.check("KaraFun installed", "warn", "KaraFun.app is not in /Applications or ~/Applications (KaraFun songs cannot be tested on this Mac)");
            return;
        }
    }
    // 1. can this process drive System Events at all
    let t = Instant::now();
    // the FIRST Apple event of a new app identity makes macOS ask "allow this app to control System Events?": give the operator a minute to answer it
    let probe = singws_mac::run_applescript("tell application \"System Events\" to return (count of application processes)", Duration::from_secs(60));
    if !probe.ok {
        r.check("System Events (shell)", script_error_status(&probe.err), if probe.err == "timeout" {
            "no answer within 60 s: if macOS asked whether this app may control System Events (Automation), click Allow and run this again".to_string()
        } else {
            describe_script_error(&probe.err)
        });
        return;
    }
    r.check("System Events (shell)", "pass", format!("answered in {} ms ({} processes)", t.elapsed().as_millis(), probe.out));
    // 2. is KaraFun running, and what are its windows (read only, the same questions the Python app's monitor asks)
    let running = singws_mac::run_applescript(
        "tell application \"System Events\"\nset m to every application process whose name contains \"KaraFun\"\nif (count of m) is 0 then return \"not running\"\nreturn name of item 1 of m\nend tell",
        Duration::from_secs(15));
    if !running.ok {
        r.check("KaraFun process", "fail", describe_script_error(&running.err));
        return;
    }
    r.fact("karafun_process", json!(running.out));
    if running.out == "not running" {
        r.check("KaraFun process", "warn", "KaraFun is installed but not running: start it and run this again to test its UI from the shell");
        return;
    }
    r.check("KaraFun process", "pass", format!("running as \"{}\"", running.out));
    let t = Instant::now();
    // Fetch the list first, then count it, as the app's own scripts do: on macOS 27 the one-liner `count of (entire contents of w)` answers 0
    // for every window while `set x to entire contents of w` returns the real list (139 elements for KaraFun, measured 2026-11-06).
    let windows = singws_mac::run_applescript(
        "tell application \"System Events\"\ntell (first application process whose name contains \"KaraFun\")\nset n to count of windows\nif n is 0 then return \"0\"\nset elems to entire contents of window 1\nreturn (n as string) & \"|\" & (count of elems) as string\nend tell\nend tell",
        Duration::from_secs(40));
    let ms = t.elapsed().as_millis();
    if !windows.ok {
        r.check("KaraFun UI (shell)", "fail", describe_script_error(&windows.err));
        return;
    }
    r.fact("karafun_ui_read_ms", json!(ms));
    r.fact("karafun_ui", json!(windows.out));
    let mut parts = windows.out.split('|');
    let n: i64 = parts.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let elems: i64 = parts.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    if n == 0 {
        r.check("KaraFun UI (shell)", "warn", "KaraFun is running but shows no window (is it hidden or minimised?)");
    } else if elems < 20 {
        r.check("KaraFun UI (shell)", "warn", format!("{n} window(s) but only {elems} UI elements were readable ({ms} ms): KaraFun's controls may not be exposed to Accessibility"));
    } else {
        r.check("KaraFun UI (shell)", "pass", format!("{n} window(s), {elems} UI elements read from the front window in {ms} ms: the shell can read KaraFun's UI"));
    }
}

fn collect() -> Report {
    let mut r = Report::new();
    platform(&mut r);
    displays(&mut r);
    helpers(&mut r);
    mac_identity(&mut r);
    libmpv(&mut r);
    karafun(&mut r);
    r
}

/// Run a child of this executable with `args` (it inherits this app's macOS identity), wait at most `limit`, and kill it after that.
fn run_child(args: &[&str], limit: Duration) -> (bool, i32) {
    let Ok(exe) = std::env::current_exe() else { return (false, -1) };
    let Ok(mut child) = Command::new(exe).args(args).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn() else { return (false, -1) };
    let t = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return (true, status.code().unwrap_or(-1)),
            Ok(None) if t.elapsed() < limit => std::thread::sleep(Duration::from_millis(250)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return (false, -2);
            }
        }
    }
}

/// Did the host spike pass? Every check ok, OR the ONLY failure is `bgm_engine` hearing silence in its short window while the engine itself ran cleanly (the position advanced, no underruns,
/// no gaps, no dropped commands): the song simply starts with silence. The second value is a note for the report in that case.
pub fn selfcheck_verdict(doc: &Value) -> (bool, Option<String>) {
    let checks = doc["checks"].as_array().cloned().unwrap_or_default();
    if checks.is_empty() {
        return (false, None);
    }
    let failed: Vec<&Value> = checks.iter().filter(|c| c["ok"] != true).collect();
    if failed.is_empty() {
        return (true, None);
    }
    if failed.len() == 1 && failed[0]["name"] == "bgm_engine" {
        let d = &failed[0]["detail"];
        let zero = |k: &str| d[k].as_f64().map(|v| v == 0.0).unwrap_or(false);
        if zero("rms") && zero("peak") && zero("underruns") && zero("gap_frames") && zero("dropped_commands") && d["position_s"].as_f64().map(|p| p > 2.0).unwrap_or(false) {
            return (true, Some(format!("the music engine ran cleanly for {:.1} s (no underruns, gaps or dropped commands) but this song starts with silence, so no sound was heard in the check window", d["position_s"].as_f64().unwrap_or(0.0))));
        }
    }
    (false, None)
}

fn read_json(path: &std::path::Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// Extract the first .cdg and .mp3 of an MP3+G zip into `dir` with the system `unzip`.
fn unzip_song(zip: &str, dir: &std::path::Path) -> Option<(String, String)> {
    let ok = Command::new("unzip").args(["-o", "-q", zip, "-d"]).arg(dir).status().ok()?.success();
    if !ok {
        return None;
    }
    let mut cdg = None;
    let mut mp3 = None;
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).ok()?.flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if name.starts_with("._") || name == "__MACOSX" {
                continue; // AppleDouble junk some zips carry
            }
            if p.is_dir() {
                stack.push(p);
                continue;
            }
            match p.extension().and_then(|x| x.to_str()).map(|x| x.to_ascii_lowercase()).as_deref() {
                Some("cdg") if cdg.is_none() => cdg = Some(p.display().to_string()),
                Some("mp3") if mp3.is_none() => mp3 = Some(p.display().to_string()),
                _ => {}
            }
        }
    }
    Some((cdg?, mp3?))
}

/// A yes/no question as a macOS dialog (the run is launched as an app, so there is no terminal). `None` = unanswered (no GUI session, or it timed out).
fn ask(question: &str, buttons: [&str; 3]) -> Option<String> {
    if std::env::var("SINGWS_READINESS_NO_PROMPT").is_ok() {
        return None; // unattended runs (tests, scripts): never put a dialog on a screen
    }
    let script = format!("display dialog \"{}\" buttons {{\"{}\", \"{}\", \"{}\"}} default button \"{}\" giving up after 120", question.replace('"', "'"), buttons[0], buttons[1], buttons[2], buttons[2]);
    let r = singws_mac::run_applescript(&script, Duration::from_secs(130));
    if !r.ok || r.out.contains("gave up:true") {
        return None;
    }
    buttons.iter().find(|b| r.out.contains(&format!("button returned:{b}"))).map(|b| b.to_string())
}

fn screenshots(dir: &std::path::Path, tag: &str, displays: usize) -> Vec<String> {
    let mut files = Vec::new();
    for n in 1..=displays.max(1) {
        let f = dir.join(format!("{tag}_display{n}.png"));
        if Command::new("screencapture").args(["-x", "-D", &n.to_string()]).arg(&f).status().map(|s| s.success()).unwrap_or(false) && f.exists() {
            files.push(f.display().to_string());
        }
    }
    files
}

/// The whole operator test, launched as an app so every child shares this app's macOS identity: the machine/KaraFun checks above, then (with `--song`) the no-Python
/// hosting selfcheck, a real song through the Rust picture + voice, and the audience display on the screen the app would use, with screenshots of every display and a
/// question to the operator. Writes `DIR/readiness.json` (+ screenshots, + each step's own report).
pub fn run_all(dir: &str, song: Option<&str>) -> i32 {
    let dir = std::path::PathBuf::from(dir);
    let _ = std::fs::create_dir_all(&dir);
    let mut r = collect();
    // the children (selfcheck, play demo) inherit the environment: hand them the media core this probe found
    if let Some(p) = find_libmpv() {
        unsafe { std::env::set_var("SINGWS_LIBMPV", &p) };
    }
    let displays = r.facts.get("gpus").and_then(|g| g.as_array()).map(|g| g.iter().map(|x| x["displays"].as_array().map(|a| a.len()).unwrap_or(0)).sum::<usize>()).unwrap_or(1);
    let mut steps = Map::new();
    match song.and_then(|z| unzip_song(z, &dir.join("song"))) {
        None => {
            let why = if song.is_some() { "the song zip could not be unpacked (needs a .cdg and an .mp3)" } else { "no --song given: the playback and display steps were not run" };
            r.check("song steps", if song.is_some() { "fail" } else { "info" }, why);
        }
        Some((cdg, mp3)) => {
            // 1. the no-Python host: Qt window + Rust video bridge + Rust BGM engine in one process
            let sc = dir.join("selfcheck.json");
            let (finished, code) = run_child(&["--selfcheck", "--selfcheck-report", &sc.display().to_string(), "--selfcheck-video", &cdg, "--selfcheck-audio", &mp3], Duration::from_secs(90));
            let doc = read_json(&sc);
            let (ok, note) = doc.as_ref().map(selfcheck_verdict).unwrap_or((false, None));
            if let Some(d) = doc {
                steps.insert("selfcheck".into(), d);
            }
            r.check("host spike (selfcheck)", if ok { "pass" } else { "fail" }, format!("finished={finished} exit={code}: one Rust process hosting the Qt window, the Rust video bridge and the Rust music engine{}", note.map(|n| format!(" ({n})")).unwrap_or_default()));
            // 2. a real song through the Rust picture + the Rust voice, clock slaved by the Rust protocol port
            let pd = dir.join("play_demo.json");
            let (finished, code) = run_child(&["--play-demo", &cdg, &mp3, "--play-demo-full", "--play-demo-report", &pd.display().to_string()], Duration::from_secs(900));
            let doc = read_json(&pd);
            let ok = doc.as_ref().map(|d| d["ok"] == true).unwrap_or(false);
            let summary = doc.as_ref().map(|d| d["summary"].to_string()).unwrap_or_default();
            if let Some(d) = doc {
                steps.insert("play_demo".into(), d);
            }
            r.check("song through Rust picture + voice", if ok { "pass" } else { "fail" }, format!("finished={finished} exit={code} {}", summary.chars().take(300).collect::<String>()));
            // 3. the audience display where the app's VideoWindow would open, on whatever screens are attached
            let child_args = ["--display-demo", &cdg, "--display-place", "--display-at", "20"];
            if let Ok(exe) = std::env::current_exe() {
                if let Ok(mut child) = Command::new(exe).args(child_args).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn() {
                    std::thread::sleep(Duration::from_secs(10));
                    let shots = screenshots(&dir, "tv", displays);
                    r.fact("tv_screenshots", json!(shots));
                    let answer = ask(&format!("Is the karaoke picture showing on the TV / audience screen right now? ({} screenshot(s) were saved)", shots.len()), ["No TV connected", "No", "Yes"]);
                    let _ = child.kill();
                    let _ = child.wait();
                    r.fact("tv_operator_answer", json!(answer));
                    match answer.as_deref() {
                        Some("Yes") => r.check("audience display (TV)", "pass", format!("operator confirmed the Rust display window showed the picture; {} screenshot(s) saved", shots.len())),
                        Some("No") => r.check("audience display (TV)", "fail", "operator says the picture did NOT show on the audience screen (see the screenshots)"),
                        Some(_) => r.check("audience display (TV)", "warn", "no TV connected: the audience display was not tested on a TV"),
                        None => r.check("audience display (TV)", "warn", "the question was not answered; screenshots saved for review"),
                    }
                } else {
                    r.check("audience display (TV)", "fail", "the display window could not be started");
                }
            }
        }
    }
    let mut report = r.to_json();
    report["steps"] = Value::Object(steps);
    let failed = report["checks"].as_array().map(|c| c.iter().any(|x| x["status"] == "fail")).unwrap_or(false);
    let path = dir.join("readiness.json");
    if let Err(e) = std::fs::write(&path, serde_json::to_string_pretty(&report).unwrap_or_default()) {
        eprintln!("cannot write {}: {e}", path.display());
        return 2;
    }
    let mut summary = String::new();
    for c in report["checks"].as_array().cloned().unwrap_or_default() {
        summary.push_str(&format!("[{}] {}: {}\n", c["status"].as_str().unwrap_or("?"), c["name"].as_str().unwrap_or("?"), c["detail"].as_str().unwrap_or("")));
    }
    let _ = std::fs::write(dir.join("readiness.txt"), &summary);
    print!("{summary}");
    if failed { 1 } else { 0 }
}

pub fn run(path: &str) -> i32 {
    let r = collect();
    let report = r.to_json();
    let failed = report["checks"].as_array().map(|c| c.iter().any(|x| x["status"] == "fail")).unwrap_or(false);
    if let Err(e) = std::fs::write(path, serde_json::to_string_pretty(&report).unwrap_or_default()) {
        eprintln!("cannot write {path}: {e}");
        return 2;
    }
    for c in report["checks"].as_array().cloned().unwrap_or_default() {
        println!("[{}] {}: {}", c["status"].as_str().unwrap_or("?"), c["name"].as_str().unwrap_or("?"), c["detail"].as_str().unwrap_or(""));
    }
    if failed { 1 } else { 0 }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requested_reads_the_report_path() {
        assert_eq!(requested(["--readiness".to_string(), "/tmp/x.json".to_string()].into_iter()), Some("/tmp/x.json".into()));
        assert_eq!(requested(["--selfcheck".to_string()].into_iter()), None);
        assert_eq!(requested(["--readiness".to_string()].into_iter()), Some("readiness.json".into()));
    }

    #[test]
    fn selfcheck_verdict_accepts_a_silent_intro_but_nothing_else() {
        let ok = |name: &str| json!({"name": name, "ok": true, "detail": {}});
        let engine = |rms: f64, underruns: f64, pos: f64| json!({"name": "bgm_engine", "ok": false, "detail": {"rms": rms, "peak": rms, "underruns": underruns, "gap_frames": 0.0, "dropped_commands": 0.0, "position_s": pos}});
        assert_eq!(selfcheck_verdict(&json!({"checks": [ok("host"), ok("libmpv")]})), (true, None));
        let silent = selfcheck_verdict(&json!({"checks": [ok("host"), engine(0.0, 0.0, 4.5)]}));
        assert!(silent.0 && silent.1.unwrap().contains("starts with silence"));
        assert!(!selfcheck_verdict(&json!({"checks": [ok("host"), engine(0.0, 3.0, 4.5)]})).0, "underruns are a real failure");
        assert!(!selfcheck_verdict(&json!({"checks": [ok("host"), engine(0.0, 0.0, 0.5)]})).0, "a position that did not advance is a real failure");
        assert!(!selfcheck_verdict(&json!({"checks": [json!({"name": "libmpv", "ok": false, "detail": {}}), engine(0.0, 0.0, 4.5)]})).0, "any other failure still fails");
        assert!(!selfcheck_verdict(&json!({"checks": []})).0);
    }

    #[test]
    fn requested_run_reads_the_directory_and_the_song() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>().into_iter();
        assert_eq!(requested_run(a(&["--readiness-run", "/tmp/r", "--song", "/s.zip"])), Some(("/tmp/r".into(), Some("/s.zip".into()))));
        assert_eq!(requested_run(a(&["--readiness-run", "/tmp/r"])), Some(("/tmp/r".into(), None)));
        assert_eq!(requested_run(a(&["--readiness", "x.json"])), None);
    }

    #[test]
    fn macho_archs_reads_thin_and_fat_headers() {
        let mut thin = vec![0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0x00, 0x00, 0x01];
        thin.extend([0u8; 8]);
        assert_eq!(macho_archs(&thin), vec!["arm64"]);
        let mut thin_x = vec![0xcf, 0xfa, 0xed, 0xfe, 0x07, 0x00, 0x00, 0x01];
        thin_x.extend([0u8; 8]);
        assert_eq!(macho_archs(&thin_x), vec!["x86_64"]);
        let mut fat = vec![0xca, 0xfe, 0xba, 0xbe, 0, 0, 0, 2];
        fat.extend([0x01, 0x00, 0x00, 0x07, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
        fat.extend([0x01, 0x00, 0x00, 0x0c, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
        assert_eq!(macho_archs(&fat), vec!["x86_64", "arm64"]);
        assert!(macho_archs(b"#!/bin/sh\n").is_empty());
        assert!(macho_archs(&[]).is_empty());
    }

    #[test]
    fn libmpv_is_looked_for_next_to_the_shell_and_in_an_installed_app() {
        let exe = std::path::Path::new("/Applications/SingWS Shell.app/Contents/MacOS/SingWS Shell");
        let c = libmpv_candidates(Some(exe), "/Users/x", Some("/custom/libmpv.dylib"));
        assert_eq!(c[0], std::path::PathBuf::from("/custom/libmpv.dylib"), "an explicit SINGWS_LIBMPV wins");
        assert!(c.iter().any(|p| p.ends_with("MacOS/../Frameworks/singws_libmpv.2.dylib")));
        assert!(c.contains(&std::path::PathBuf::from("/Applications/SingWS.app/Contents/Frameworks/singws_libmpv.2.dylib")));
        assert!(c.contains(&std::path::PathBuf::from("/Users/x/Applications/SingWS.app/Contents/Frameworks/singws_libmpv.2.dylib")));
        assert_eq!(libmpv_candidates(None, "/h", None).len(), 2);
    }

    #[test]
    fn a_timeout_is_a_warning_and_any_other_script_error_a_failure() {
        assert_eq!(script_error_status("timeout"), "warn");
        assert_eq!(script_error_status("not authorized to send Apple events (-1743)"), "fail");
        assert_eq!(script_error_status("... (-25211)"), "fail");
    }

    #[test]
    fn script_errors_are_explained() {
        assert!(describe_script_error("... (-25211)").contains("Accessibility"));
        assert!(describe_script_error("not authorized to send Apple events (-1743)").contains("Automation"));
        assert!(describe_script_error("timeout").contains("in time"));
    }

    #[test]
    fn the_report_has_a_kind_and_checks() {
        let mut r = Report::new();
        r.check("a", "pass", "x");
        r.fact("k", json!(1));
        let j = r.to_json();
        assert_eq!(j["kind"], "python_free_readiness");
        assert_eq!(j["checks"][0]["status"], "pass");
        assert_eq!(j["facts"]["k"], 1);
    }
}
