//! `singws-queue-state [--init JSON]`: reads event JSON lines on stdin, writes one `{"result":..,"snapshot":..}` line per event.
//! Without `--init` the whole input is read first and the merged `config` events initialise the world (what `run_stream` does);
//! with `--init` events are answered one by one (flushing after each line) so a parent process can drive it interactively.
use std::io::{BufRead, Write};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let init = args.iter().position(|a| a == "--init").and_then(|i| args.get(i + 1)).and_then(|s| serde_json::from_str::<serde_json::Value>(s).ok());
    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    if let Some(init) = init {
        let mut st = singws_queue_state::State::new(&init);
        for line in stdin.lock().lines() {
            let Ok(line) = line else { break };
            if line.trim().is_empty() {
                continue;
            }
            let Ok(ev) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
            let _ = writeln!(out, "{}", st.step(&ev));
            let _ = out.flush();
        }
        return;
    }
    let events: Vec<serde_json::Value> = stdin.lock().lines().map_while(Result::ok).filter(|l| !l.trim().is_empty()).filter_map(|l| serde_json::from_str(&l).ok()).collect();
    let init = singws_queue_state::merged_config(&events);
    let mut st = singws_queue_state::State::new(&init);
    for ev in &events {
        let _ = writeln!(out, "{}", st.step(ev));
    }
}
