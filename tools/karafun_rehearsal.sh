#!/bin/bash
# Rehearse the Rust-driven KaraFun player on this Mac and read the result.
#
#   tools/karafun_rehearsal.sh start     quit SingWS, back up settings.json, make sure karafun_web_engine is "rust", open SingWS
#   tools/karafun_rehearsal.sh steps     print the rehearsal steps (what to do in the app)
#   tools/karafun_rehearsal.sh watch     follow today's KaraFun log lines live (Ctrl-C to stop)
#   tools/karafun_rehearsal.sh report    read the log: CLEAN / PARTIAL / ATTENTION ... (add --require-all to demand CLEAN)
#   tools/karafun_rehearsal.sh restore   quit SingWS and put the backed-up settings.json back (and reopen SingWS)
#
# Paths: ~/SingWS (or $SINGWS_HOME), the app at /Applications/SingWS.app (or $SINGWS_APP).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${SINGWS_HOME:-$HOME/SingWS}"
APP="${SINGWS_APP:-/Applications/SingWS.app}"
SETTINGS="$HOME_DIR/settings.json"
BACKUP="$SETTINGS.before-rust-rehearsal"
LOG="$HOME_DIR/logs/singws_$(date +%Y-%m-%d).log"

quit_app() { pkill -f "$APP/Contents/MacOS/SingWS" 2>/dev/null || true; for _ in $(seq 1 50); do pgrep -f "$APP/Contents/MacOS/SingWS" >/dev/null || return 0; sleep 0.2; done; echo "SingWS did not quit" >&2; exit 1; }

case "${1:-}" in
  start)
    [ -f "$SETTINGS" ] || { echo "no settings file at $SETTINGS" >&2; exit 1; }
    quit_app
    [ -f "$BACKUP" ] || cp "$SETTINGS" "$BACKUP"
    python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
before = d.get("karafun_web_engine", "(not set)")
d["karafun_web_engine"] = "rust"
json.dump(d, open(p, "w"), indent=2)
print(f"karafun_web_engine: {before} -> rust")
PY
    open "$APP"
    echo "SingWS opened. Play a KaraFun song, then:  $0 steps"
    ;;
  steps)
    python3 "$HERE/tools/karafun_rust_report.py" --checklist
    ;;
  watch)
    echo "following $LOG (Ctrl-C to stop)"
    tail -n 0 -f "$LOG" | grep --line-buffered "KARAFUN"
    ;;
  report)
    shift
    python3 "$HERE/tools/karafun_rust_report.py" "$@"
    ;;
  restore)
    [ -f "$BACKUP" ] || { echo "no backup at $BACKUP" >&2; exit 1; }
    quit_app
    cp "$BACKUP" "$SETTINGS" && rm "$BACKUP"
    open "$APP"
    echo "settings.json restored from the backup; SingWS reopened"
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
