#!/bin/bash
# Read-only pre-show check for the KaraFun web player on THIS Mac. Prints OK/FAIL lines; never prints a password, email or key.
H="${SINGWS_HOME:-$HOME/SingWS}"; APP=/Applications/SingWS.app
ok(){ echo "OK   $*"; }; bad(){ echo "FAIL $*"; FAILS=1; }
echo "machine: $(uname -m)   app: $(defaults read $APP/Contents/Info.plist CFBundleShortVersionString 2>/dev/null)"
D=""; for f in "$APP/Contents/Frameworks/singws-karafun-display" "$APP/Contents/Resources/singws-karafun-display"; do [ -x "$f" ] && { D="$f"; break; }; done
[ -n "$D" ] && ok "KaraFun display program bundled" || bad "KaraFun display program NOT in the app (reinstall SingWS)"
[ -n "$D" ] && { "$D" installed && ok "Chromium framework installed" || bad "Chromium not downloaded yet (SingWS asks the first time a KaraFun song starts; about 125 MB, needs internet once)"; }
grep -Eq '"karafun_web_email": *"[^"]+' "$H/settings.json" 2>/dev/null && ok "saved login email set" || bad "no saved KaraFun login email in settings (the player then needs the browser sign-in)"
security find-generic-password -s "SingWS KaraFun account" >/dev/null 2>&1 && ok "saved login present in the Keychain" || bad "no saved login in the Keychain (re-save: python3 tools/karafun_web_save_login.py)"
curl -sI --max-time 8 https://cef-builds.spotifycdn.com/index.json | head -1 | grep -q 200 && ok "internet reaches the Chromium download host" || bad "cannot reach the Chromium download host (only needed the first time)"
curl -sI --max-time 8 https://www.karafun.com/web/ | head -1 | grep -qE "200|30[12]" && ok "internet reaches KaraFun" || bad "cannot reach karafun.com"
[ -z "$FAILS" ] && echo "ALL OK" || echo "SOME CHECKS FAILED"
