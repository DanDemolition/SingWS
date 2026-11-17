"""The Rust KaraFun driver as a real process (`singws-karafun-svc --drive`), against a scripted fake display program."""
import _test_profile  # noqa: F401
import json, os, queue, stat, subprocess, tempfile, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = Path.home() / ".cache" / "singws-rust-target-karafun-svc" / "debug" / "singws-karafun-svc"

FAKE = r'''#!/bin/bash
# fake display program: `installed` -> exit 0 (or 3 when MISSING is set); otherwise a player that is signed in, logs every command, and "plays" what it is asked
[ "$1" = "installed" ] && { [ -n "$FAKE_MISSING" ] && exit 3; exit 0; }
LOG="$FAKE_LOG"
echo '{"type":"helper","state":"started"}'
echo '{"type":"status","signedIn":true,"plan":"Plus","playing":false,"position":0,"duration":0}'
while IFS= read -r line; do
  echo "$line" >> "$LOG"
  case "$line" in
    *'"cmd": "play"'*|*'"cmd":"play"'*) [[ $line =~ \"id\":\ *([0-9]+) ]]; echo '{"type":"status","signedIn":true,"playing":true,"state":"playing","position":1.0,"duration":30.0,"song":{"id":'"${BASH_REMATCH[1]}"'}}';;
    *'"cmd":"endsong"'*|*'"cmd": "endsong"'*) echo '{"type":"status","signedIn":true,"playing":false,"state":"idle","position":30.0,"duration":30.0,"song":{"id":3241559}}';;
    *'"cmd":"quit"'*|*'"cmd": "quit"'*) exit 0;;
  esac
done
'''


@unittest.skipUnless(BIN.exists(), "build the svc binary first (cargo build -p singws-karafun-svc)")
class DriverProcess(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kfdrive_")
        self.fake = os.path.join(self.dir, "fake_display.sh")
        Path(self.fake).write_text(FAKE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.dir, "display.log")
        Path(self.log).write_text("")
        self.events: "queue.Queue[dict]" = queue.Queue()

    def start(self, **env):
        e = dict(os.environ, FAKE_LOG=self.log, **env)
        self.proc = subprocess.Popen([str(BIN), "--drive", "--display", self.fake], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=e, text=True)
        def pump():
            for line in self.proc.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    pass
        threading.Thread(target=pump, daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def send(self, **m):
        self.proc.stdin.write(json.dumps(m) + "\n")
        self.proc.stdin.flush()

    def wait_for(self, pred, timeout=15.0):
        end, seen = time.time() + timeout, []
        while time.time() < end:
            try:
                ev = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append(ev)
            if pred(ev):
                return ev
        self.fail(f"not seen in {timeout}s; saw {seen}")

    def display_log(self):
        return [json.loads(l) for l in Path(self.log).read_text().splitlines() if l.strip()]

    SONG = dict(op="start_song", seq=1, song_id=58970, enabled=True, key=0, tempo=100, title="Linoleum",
                play={"query": "NOFX Linoleum", "queries": ["NOFX Linoleum"], "artist": "NOFX", "title": "Linoleum"})

    def test_a_song_plays_after_the_countdown_hold_and_the_picture_starts(self):
        self.start()
        t0 = time.time()
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 1 and e["ok"] is True)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
        waited = time.time() - t0
        self.assertGreaterEqual(waited, 3.4, "the play is held for the countdown animation")
        plays = [c for c in self.display_log() if c.get("cmd") == "play"]
        self.assertEqual(len(plays), 1)
        self.assertEqual((plays[0]["id"], plays[0]["artist"]), (58970, "NOFX"))

    def test_chromium_missing_is_reported_not_played(self):
        self.start(FAKE_MISSING="1")
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "state" and e["service"] == "needs_browser")
        self.assertEqual([c for c in self.display_log() if c.get("cmd") == "play"], [])

    def test_the_display_program_dying_mid_song_is_restarted_and_the_song_resumed(self):
        self.start()
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
        self.send(op="display", command={"cmd": "quit"})       # the fake exits on quit
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "notify" and "restarting" in e["effect"][1], timeout=20)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "entry_status" and e["effect"][1] == "playing", timeout=20)
        plays = [c for c in self.display_log() if c.get("cmd") == "play"]
        self.assertEqual(len(plays), 2, "the same song is played again by a fresh player")
        self.assertEqual({p["id"] for p in plays}, {58970})

    def test_no_message_is_ever_declined_by_the_song_machine(self):
        self.start()
        self.send(**self.SONG)
        self.send(op="display", command={"cmd": "quit"})
        seen = []
        end = time.time() + 8
        while time.time() < end:
            try:
                seen.append(self.events.get(timeout=0.3))
            except queue.Empty:
                pass
        self.assertEqual([e for e in seen if e.get("ev") == "effect" and "declined" in json.dumps(e)], [])


if __name__ == "__main__":
    unittest.main()
