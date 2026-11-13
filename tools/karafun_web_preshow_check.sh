#!/bin/bash
# Read-only pre-show check for the KaraFun web player on THIS Mac. Prints OK/FAIL lines; never prints a password, email or key.
H="${SINGWS_HOME:-$HOME/SingWS}"; APP=/Applications/SingWS.app
ok(){ echo "OK   $*"; }; bad(){ echo "FAIL $*"; FAILS=1; }
echo "machine: $(uname -m)   app: $(defaults read $APP/Contents/Info.plist CFBundleShortVersionString 2>/dev/null)"
C=$(ls -d "$H"/Chrome-for-Testing/*/ 2>/dev/null | while read d; do [ -f "$d/.complete" ] && echo "$d"; done | tail -1)
[ -n "$C" ] && ok "player browser downloaded: $(basename "$C")" || bad "player browser NOT downloaded yet (needs internet once, ~200 MB; start SingWS once with internet and wait for 'Signed in')"
[ -n "$C" ] && { arch=$(file "$C"/chrome-*/*.app/Contents/MacOS/* 2>/dev/null | head -1); echo "$arch" | grep -q "$(uname -m | sed s/x86_64/x86_64/)" && ok "browser matches this Mac's architecture" || bad "browser architecture does not match this Mac ($(uname -m))"; }
for f in "$APP/Contents/Frameworks/singws-karafun-web" "$APP/Contents/Resources/singws-karafun-web" ; do [ -x "$f" ] && { ok "player helper bundled"; H1=1; break; }; done; [ -z "$H1" ] && bad "player helper not found in the app"
python3 - "$H" <<'PY'
import json,sys,os
p=os.path.join(sys.argv[1],"settings.json")
try: s=json.load(open(p))
except Exception as e: print("FAIL settings.json unreadable"); sys.exit()
print("OK   saved login email set" if s.get("karafun_web_email") else "FAIL no saved KaraFun login email in settings (the player then needs the browser sign-in)")
PY
security find-generic-password -s "SingWS KaraFun account" >/dev/null 2>&1 && ok "saved login present in the Keychain" || bad "no saved login in the Keychain (re-save: python3 tools/karafun_web_save_login.py)"
curl -sI --max-time 8 https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json | head -1 | grep -q 200 && ok "internet reaches Google's browser index" || bad "cannot reach Google's browser index (only matters if the browser is not downloaded yet)"
curl -sI --max-time 8 https://www.karafun.com/web/ | head -1 | grep -qE "200|30[12]" && ok "internet reaches KaraFun" || bad "cannot reach karafun.com"
[ -z "$FAILS" ] && echo "ALL OK" || echo "SOME CHECKS FAILED"
