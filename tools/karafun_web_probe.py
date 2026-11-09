#!/usr/bin/env python3
"""Hand check of karafun_web.py (nothing in SingWS uses it yet): start Chrome headless on KaraFun's web player and print what the page says.

  python3 tools/karafun_web_probe.py                       # 25 s with a throw-away profile (not signed in): ready? status? picture frames?
  python3 tools/karafun_web_probe.py --profile DIR         # a profile that keeps the sign-in (use ~/SingWS/Chrome-KaraFun)
  python3 tools/karafun_web_probe.py --profile DIR --signin   # asks the page to sign in: KaraFun's "Add a TV" page opens in your browser
  python3 tools/karafun_web_probe.py --profile DIR --play 3241559 "NOFX Linoleum" --seconds 60   # play song kf_3241559 (needs a signed-in profile)
Chrome path: --chrome PATH (default: Google Chrome in /Applications).
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import karafun_web  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--chrome")
    ap.add_argument("--seconds", type=int, default=25)
    ap.add_argument("--signin", action="store_true")
    ap.add_argument("--play", nargs=2, metavar=("ID", "QUERY"))
    ap.add_argument("--screencast", action="store_true", help="count picture frames at 1280x720")
    args = ap.parse_args()
    profile = args.profile or tempfile.mkdtemp(prefix="kfweb-probe-")
    state = {"frames": 0, "bytes": 0, "status": 0, "ready": False, "last": None, "errors": []}

    def on_message(m):
        t = m.get("type")
        if t == "status":
            state["status"] += 1
            key = (m.get("signedIn"), m.get("state"), (m.get("song") or {}).get("id"), int(m.get("position") or 0) // 10)
            if key != state["last"]:
                state["last"] = key
                print("status", {k: m.get(k) for k in ("signedIn", "plan", "playing", "state", "position", "duration", "queue", "song")}, flush=True)
        else:
            if t == "ready":
                state["ready"] = True
            if t == "error":
                state["errors"].append(m.get("message"))
            print(t, {k: v for k, v in m.items() if k != "type"}, flush=True)

    def on_frame(b):
        state["frames"] += 1
        state["bytes"] += len(b)

    k = karafun_web.KaraFunWeb(profile, on_message=on_message, on_frame=on_frame, on_log=lambda s: print("log:", s, flush=True), chrome_path=args.chrome)
    print("helper:", k.helper_path, "chrome:", args.chrome or karafun_web.find_chrome(), "profile:", profile)
    k.start()
    if args.screencast:
        k.start_screencast(1280, 720)
    start = time.monotonic()
    did = False
    while time.monotonic() - start < args.seconds:
        time.sleep(0.5)
        if state["ready"] and not did:
            did = True
            if args.signin:
                k.sign_in()
            if args.play:
                k.play(int(args.play[0]), args.play[1])
    print(f"summary: ready={state['ready']} statuses={state['status']} frames={state['frames']} ({state['bytes']//1024} KB) errors={state['errors']}")
    k.close()


main()
