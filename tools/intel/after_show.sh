#!/bin/bash
# Run ON THE INTEL MAC after the show (SingWS quit): bundles the evidence for the shadow report into ONE zip on the Desktop. Read-only.
# The zip holds the newest log and a REDUCED settings file (only the flag values the report needs: no keys, PINs, URLs or names).
set -euo pipefail
HOME_DIR="${SINGWS_HOME:-$HOME/SingWS}"; OUT_DIR="${OUT_DIR:-$HOME/Desktop}"
L=$(ls -t "$HOME_DIR"/logs/singws_*.log 2>/dev/null | head -1); [[ -n "$L" ]] || { echo "No log found."; exit 1; }
T=$(mktemp -d); D="$T/singws-show-$(date +%Y%m%d-%H%M)"; mkdir -p "$D"
cp "$L" "$D/"
{ echo "{"; grep -E '^  "(transport_shadow|transport_drive|transport_drive_media_end|transport_drive_watchdog|queue_shadow|plan_shadow|settings_shadow|store_shadow|wire_shadow|publish_live_state|shell_commands|rust_evidence_defaults_migrated|transport_shadow_default_on_migrated)":' "$HOME_DIR/settings.json" | sed -E 's/,[[:space:]]*$//' | paste -sd, - | sed 's/^/ /'; echo "}"; } > "$D/settings.json"
( cd "$T" && ditto -c -k --keepParent "$(basename "$D")" "$OUT_DIR/$(basename "$D").zip" )
echo "Made $OUT_DIR/$(basename "$D").zip ($(du -h "$OUT_DIR/$(basename "$D").zip" | cut -f1)). Send me that one file."
echo "It contains: $(basename "$L") and settings.json with $(grep -o '":' "$D/settings.json" | wc -l | tr -d ' ') flag values."
echo "Log lines never contain hosts, keys or values (only endpoint file names)."
