//! `singws-mac-probe`: prints what the platform shim sees, as text (default) or one JSON object (`--json`, used by the app's
//! `platform_shadow`). Never prompts. `--applescript SOURCE` runs a script in this process; the default script is `return 1+1`.
//! `--location` also tries a one-shot location fix (it still only reads status unless this binary is already authorized).
use singws_mac::*;
use std::time::Duration;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--json") {
        println!("{}", shadow_state_json());
        return;
    }
    // `--hold SECONDS`: hold the keep-awake assertion that long (so `pmset -g assertions` can be read), then release it.
    if let Some(secs) = args.iter().position(|a| a == "--hold").and_then(|i| args.get(i + 1)).and_then(|v| v.parse::<f64>().ok()) {
        let began = keep_awake_begin("singws-mac-probe hold");
        println!("held={began} active={}", keep_awake_active());
        use std::io::Write;
        let _ = std::io::stdout().flush();
        std::thread::sleep(Duration::from_secs_f64(secs.clamp(0.0, 120.0)));
        keep_awake_end();
        println!("released active={}", keep_awake_active());
        let _ = std::io::stdout().flush();
        std::thread::sleep(Duration::from_secs(2)); // stay alive after the release so an outside observer can see it is really gone
        return;
    }
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!("usage: singws-mac-probe [--json] [--hold SECONDS] [--applescript SOURCE] [--location]");
        return;
    }
    let script = args.iter().position(|a| a == "--applescript").and_then(|i| args.get(i + 1)).cloned().unwrap_or_else(|| "return 1+1".into());
    println!("singws-mac-probe {} arch={} supported={}", env!("CARGO_PKG_VERSION"), std::env::consts::ARCH, supported());
    println!("exe: {}", std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default());
    println!("frontmost_app: {:?}", frontmost_app_name());
    println!("ax_is_trusted: {}", ax_is_trusted());
    let began = keep_awake_begin("singws-mac-probe");
    println!("keep_awake: begin={} active={}", began, keep_awake_active());
    keep_awake_end();
    println!("keep_awake: after end active={}", keep_awake_active());
    let r = run_applescript(&script, Duration::from_secs(20));
    println!("applescript {:?}: ok={} out={:?} err={:?}", script, r.ok, r.out, r.err);
    let status = location_status();
    println!("location_status: {} ({})", status, location_status_name(status));
    if args.iter().any(|a| a == "--location") {
        match device_location_once(Duration::from_secs(10)) {
            Ok((lat, lon, acc)) => println!("location: lat={lat:.4} lon={lon:.4} accuracy_m={acc:.0}"),
            Err(e) => println!("location: unavailable ({e})"),
        }
    }
}
