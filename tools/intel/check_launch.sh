#!/bin/bash
# Run ON THE INTEL MAC after opening the new SingWS once: shows whether the Rust evidence collectors came up. Read-only.
HOME_DIR="${SINGWS_HOME:-$HOME/SingWS}"; L=$(ls -t "$HOME_DIR"/logs/singws_*.log 2>/dev/null | head -1)
[[ -n "$L" ]] || { echo "No log found in $HOME_DIR/logs. Open SingWS first."; exit 1; }
echo "log: $L"; echo
ok() { n=$(grep -c "$2" "$L" || true); printf '%-30s %s\n' "$1" "$([[ "$n" -gt 0 ]] && echo "OK ($n)" || echo "MISSING")"; }
grep -m1 '\[LAUNCH\]' "$L" | sed -E 's/.*"version":"([^"]*)".*/version \1/'; echo
ok "settings migrated" 'RUST-DEFAULTS\] migrated\|rust_evidence'
ok "store shadow" 'STORE-SHADOW\] enabled available=1'
ok "wire shadow" 'WIRE-SHADOW\] enabled available=1'
ok "settings shadow" 'SETTINGS-SHADOW\] enabled available=1'
ok "shell commands (dry run)" 'SHELL-CMD\] ready mode=dry_run'
ok "Rust background engine" 'Rust background engine ready'
echo; echo "tracebacks: $(grep -c Traceback "$L" || true)"
echo "live_state.json: $( [[ -f "$HOME_DIR/live_state.json" ]] && echo present || echo 'not written yet (appears once a show is open)')"
echo "MISSING on the migration line is normal ONLY if the profile was migrated on an earlier launch (then check settings.json says \"rust_evidence_defaults_migrated\": true)."
