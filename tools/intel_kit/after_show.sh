#!/bin/bash
# Run ON THE INTEL MAC after the show (SingWS quit): bundles the evidence for the shadow report into ONE zip on the Desktop. Read-only.
# The zip holds the newest log and a REDUCED settings file (only the flag values the report needs: no keys, PINs, URLs or names).
set -euo pipefail
HOME_DIR="${SINGWS_HOME:-$HOME/SingWS}"; OUT_DIR="${OUT_DIR:-$HOME/Desktop}"
LOGS=$(find "$HOME_DIR/logs" -name "singws_*.log*" -mtime -3 2>/dev/null | sort | tail -4); [[ -n "$LOGS" ]] || { echo "No log found."; exit 1; }
T=$(mktemp -d); D="$T/singws-show-$(date +%Y%m%d-%H%M)"; mkdir -p "$D"
for f in $LOGS; do cp "$f" "$D/"; done
# Native crash reports (the app log only catches Python errors): SingWS/Python .ips files from the last 3 days, if any.
IPS=$(find "$HOME/Library/Logs/DiagnosticReports" -maxdepth 1 \( -name "SingWS*.ips" -o -name "Python*.ips" -o -name "singws-shell*.ips" \) -mtime -3 2>/dev/null | head -6)
if [[ -n "$IPS" ]]; then mkdir -p "$D/crash-reports"; for f in $IPS; do cp "$f" "$D/crash-reports/"; done; fi
{ echo "{"; grep -E '^  "(transport_shadow|transport_drive|transport_drive_media_end|transport_drive_watchdog|queue_shadow|plan_shadow|start_shadow|start_drive|scan_engine|platform_shadow|logpack_shadow|relay_msg_shadow|waitlist_snapshot|stall_stack_capture|settings_shadow|queue_state_shadow|store_shadow|wire_shadow|publish_live_state|shell_commands|queue_mode|karaoke_audio_engine|rust_evidence_defaults_migrated|transport_shadow_default_on_migrated)":' "$HOME_DIR/settings.json" | sed -E 's/,[[:space:]]*$//' | paste -sd, - | sed 's/^/ /'; echo "}"; } > "$D/settings.json"
# The newest Python-free readiness run (check_python_free.sh), if any: EVERYTHING in its folder except the extracted song copy (song/, megabytes): the report, the summary, the
# selfcheck/play-demo results with their bridge logs, and the screenshots. Included even when the run died before writing readiness.json, so a failed run is never lost.
RD=$(ls -dt "$HOME_DIR"/readiness/*/ 2>/dev/null | head -1 || true)
READINESS="none"
if [[ -n "${RD:-}" ]]; then
  mkdir -p "$D/readiness"
  ( cd "$RD" && find . -type f -not -path "./song/*" -print0 | while IFS= read -r -d '' f; do mkdir -p "$D/readiness/$(dirname "$f")"; cp "$f" "$D/readiness/$f"; done ) 2>/dev/null || true
  READINESS="$(basename "$RD") ($(find "$D/readiness" -type f | wc -l | tr -d ' ') files)"
fi
( cd "$T" && ditto -c -k --keepParent "$(basename "$D")" "$OUT_DIR/$(basename "$D").zip" )
echo "Made $OUT_DIR/$(basename "$D").zip ($(du -h "$OUT_DIR/$(basename "$D").zip" | cut -f1)). Send me that one file."
echo "It contains: $(for f in $LOGS; do printf "%s " "$(basename "$f")"; done)and settings.json with $(grep -o '":' "$D/settings.json" | wc -l | tr -d ' ') flag values."
echo "Readiness run included: $READINESS"
echo "Crash reports included: $(if [[ -n "$IPS" ]]; then echo "$IPS" | wc -l | tr -d " "; else echo none; fi)"
echo "Log lines never contain hosts, keys or values (only endpoint file names)."
