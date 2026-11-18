"""The Rust KaraFun driver as a real process (`singws-karafun-svc --drive`), against a scripted fake display program."""
import _test_profile  # noqa: F401
import json, os, queue, stat, subprocess, tempfile, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = Path(os.environ.get("SINGWS_KF_SVC_BIN") or Path.home() / ".cache" / "singws-rust-target-karafun-svc" / "debug" / "singws-karafun-svc")  # the mutation tool points this at the mutated build

FAKE = r'''#!/bin/bash
# fake display program: `installed` -> exit 0 (or 3 when MISSING is set); otherwise a player that is signed in, logs every command, and "plays" what it is asked
[ "$1" = "installed" ] && { [ -n "$FAKE_MISSING" ] && exit 3; exit 0; }
LOG="$FAKE_LOG"
[ -n "$FAKE_ARGV" ] && echo "$@" > "$FAKE_ARGV"
echo '{"type":"helper","state":"started"}'
echo 'not json at all'
echo '123'
if [ -n "$FAKE_SIGNEDOUT" ]; then echo '{"type":"status","signedIn":false,"playing":false,"position":0,"duration":0}'; [ -n "$FAKE_SIGNEDOUT_LOOP" ] && ( for i in $(seq 1 60); do sleep 0.05; echo '{"type":"status","signedIn":false,"playing":false,"position":0,"duration":0}'; done ) & else echo '{"type":"status","signedIn":true,"plan":"Plus","playing":false,"position":0,"duration":0}'; fi
echo '{"type":"log","message":"second object after a non-object line"}'
while IFS= read -r line; do
  echo "$line" >> "$LOG"
  case "$line" in
    *'"cmd": "play"'*|*'"cmd":"play"'*) [[ $line =~ \"id\":\ *([0-9]+) ]]; echo '{"type":"status","signedIn":true,"playing":true,"state":"playing","position":1.0,"duration":30.0,"song":{"id":'"${BASH_REMATCH[1]}"'}}';;
    *'"cmd":"seekend"'*|*'"cmd": "seekend"'*) ( for i in $(seq 1 80); do echo '{"type":"status","signedIn":true,"playing":false,"state":"idle","position":30.0,"duration":30.0,"song":{"id":3241559}}'; sleep 0.2; done ) & ;;
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

    def start(self, extra=(), **env):
        e = dict(os.environ, FAKE_LOG=self.log, **env)
        self.proc = subprocess.Popen([str(BIN), "--drive", "--display", self.fake, *extra], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=e, text=True)
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

    def drain(self, seconds):
        seen, end = [], time.time() + seconds
        while time.time() < end:
            try:
                seen.append(self.events.get(timeout=0.2))
            except queue.Empty:
                pass
        return seen

    def effect_names(self, events):
        return [e["effect"][0] for e in events if e.get("ev") == "effect"]

    def test_the_loop_keeps_serving_after_every_op_and_answers_each_one(self):
        self.start()
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 1 and e["ok"] is True)
        self.send(op="world", entry_current=True, active_same=True)
        self.send(op="finish", seq=2)
        self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 2)
        self.send(op="no_such_op", seq=3)
        done = self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 3)
        self.assertIs(done["ok"], False, "an unknown op is answered with ok=false")
        self.send(op="toggle_pause", seq=4)
        self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 4)
        self.assertIsNone(self.proc.poll(), "the process is still running after four ops")

    def test_every_display_line_after_a_non_object_line_still_reaches_the_app(self):
        self.start()
        self.send(**self.SONG)
        seen = self.drain(2.0)
        statuses = [e for e in seen if e.get("ev") == "status"]
        self.assertGreaterEqual(len(statuses), 1, "the display's status is passed to the app")
        self.assertTrue(all(e["message"].get("type") == "status" for e in statuses), "only status lines are passed as status events")
        self.assertTrue(any(e.get("ev") == "effect" and e["effect"][0] == "page_message" and e["effect"][1].get("type") == "log" for e in seen),
                        "a line that follows a non-object line is still read: " + json.dumps(seen)[:400])

    def test_state_changes_are_announced_once_each(self):
        self.start()
        self.send(**self.SONG)
        seen = self.drain(2.0)
        states = [(e["service"], tuple(e["host"])) for e in seen if e.get("ev") == "state"]
        self.assertGreaterEqual(len(states), 2)
        self.assertEqual(len(states), len(set(states)), f"no state is announced twice in a row: {states}")
        self.assertTrue(all(a != b for a, b in zip(states, states[1:])))

    def test_shutdown_ends_the_process_with_status_zero(self):
        self.start()
        self.send(op="shutdown")
        self.assertEqual(self.proc.wait(timeout=8), 0)

    def test_closing_the_input_ends_the_process_with_status_zero(self):
        self.start()
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=8), 0)

    def test_without_a_display_path_it_refuses_with_status_two(self):
        p = subprocess.run([str(BIN), "--drive"], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 2)

    def test_the_profile_folder_is_handed_to_the_display_program(self):
        argv = os.path.join(self.dir, "argv.txt")
        prof = os.path.join(self.dir, "profile")
        os.makedirs(prof)
        e = dict(os.environ, FAKE_LOG=self.log, FAKE_ARGV=argv)
        self.proc = subprocess.Popen([str(BIN), "--drive", "--display", self.fake, "--profile", prof, "--email", ""], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=e, text=True)
        threading.Thread(target=lambda: [self.events.put(json.loads(l)) for l in self.proc.stdout if l.startswith("{")], daemon=True).start()
        self.addCleanup(self.stop)
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "done")
        time.sleep(0.5)
        self.assertEqual(Path(argv).read_text().split(), ["--profile", prof])

    def test_a_time_stamped_op_sets_the_apps_clock(self):
        self.start()
        self.send(t=5000.0, **self.SONG)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
        seen = self.drain(0.5)
        walls = [e["effect"] for e in seen if e.get("ev") == "effect" and e["effect"][0] == "entry_set" and "clock_started_at" in e["effect"][1]]
        times = [w[2] for w in walls]
        self.assertTrue(times, f"no timed effect seen: {self.effect_names(seen)}")
        self.assertTrue(any(4990 <= x <= 5100 for x in times), f"entry times are in the app's clock (about 5000): {walls}")

    def test_missing_key_and_tempo_default_to_zero_and_one_hundred(self):
        self.start()
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
        self.send(op="key_tempo", seq=7)
        self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 7)
        time.sleep(0.4)
        log = self.display_log()
        keys = [c["semitones"] for c in log if c.get("cmd") == "key"]
        tempos = [c["percent"] for c in log if c.get("cmd") == "tempo"]
        self.assertEqual((keys[-1:], tempos[-1:]), ([0], [100]), log)

    def test_world_defaults_match_saying_true_and_differ_from_saying_false(self):
        def run(**world):
            self.setUp()
            self.start()
            self.send(**self.SONG)
            self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
            self.send(op="world", **world)
            self.send(op="display", command={"cmd": "quit"})
            seen = self.drain(4.0)
            self.stop()
            return [e["effect"][0] for e in seen if e.get("ev") == "effect"]
        default = run()
        explicit_true = run(entry_current=True, active_same=True)
        self.assertEqual(default, explicit_true)
        self.assertNotEqual(default, run(entry_current=False, active_same=False), "the test must be able to tell false from true")

    def test_the_apps_clock_offset_does_not_drift_with_the_time_the_process_has_run(self):
        self.start()
        time.sleep(2.0)                                   # the process has run for a while before the first timed op
        self.send(t=5000.0, **self.SONG)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
        seen = self.drain(0.3)
        stamps = [e["effect"][2] for e in seen if e.get("ev") == "effect" and e["effect"][0] == "entry_set" and "clock_started_at" in e["effect"][1]]
        self.assertTrue(stamps)
        self.assertTrue(all(5002.5 <= x <= 5005.5 for x in stamps), f"the entry time is the app's t (5000) plus the ~3.5 s hold, not drifted by the 2 s the process had run: {stamps}")

    def test_a_display_program_that_cannot_run_is_reported_as_missing_not_as_a_crash(self):
        e = dict(os.environ, FAKE_LOG=self.log)
        self.proc = subprocess.Popen([str(BIN), "--drive", "--display", os.path.join(self.dir, "no_such_program")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=e, text=True)
        threading.Thread(target=lambda: [self.events.put(json.loads(l)) for l in self.proc.stdout if l.startswith("{")], daemon=True).start()
        self.addCleanup(self.stop)
        self.send(**self.SONG)
        self.wait_for(lambda e: e.get("ev") == "state" and e["service"] == "needs_browser")

    def fake_security(self, exit_code=0, password="secret"):
        """A stand-in for /usr/bin/security (the debug build reads SINGWS_SECURITY_BIN): logs its arguments and answers `find-generic-password -w` like the Keychain does."""
        path, calls = os.path.join(self.dir, "fake_security.sh"), os.path.join(self.dir, "security.calls")
        Path(path).write_text('#!/bin/bash\necho "$@" >> "%s"\n[ %d -ne 0 ] && exit %d\necho "h:%s"\n' % (calls, exit_code, exit_code, password.encode().hex()))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        Path(calls).write_text("")
        return path, calls

    def logins(self):
        return [json.loads(l) for l in Path(self.log).read_text().splitlines() if l.strip() and '"login"' in l]

    def test_signed_out_the_saved_login_is_read_from_the_keychain_and_sent_to_the_display_only(self):
        sec, calls = self.fake_security()
        self.start(extra=["--email", "dj@example.com"], FAKE_SIGNEDOUT="1", FAKE_SIGNEDOUT_LOOP="1", SINGWS_SECURITY_BIN=sec)
        self.send(op="retry_service")
        end = time.time() + 15
        while time.time() < end and not self.logins():
            time.sleep(0.1)
        self.assertEqual(len(self.logins()), 1, self.display_log())
        login = self.logins()[0]
        self.assertEqual((login["cmd"], login["email"], login["password"]), ("login", "dj@example.com", "secret"))
        self.assertIn("find-generic-password -a dj@example.com", Path(calls).read_text())
        seen = []
        while not self.events.empty():
            seen.append(self.events.get_nowait())
        self.assertNotIn("secret", json.dumps(seen), "the password never reaches the app")

    def test_signed_out_with_no_email_or_an_unreadable_keychain_no_login_is_sent(self):
        sec, calls = self.fake_security()
        self.start(FAKE_SIGNEDOUT="1", FAKE_SIGNEDOUT_LOOP="1", SINGWS_SECURITY_BIN=sec)       # no --email
        self.send(op="retry_service")
        time.sleep(4.5)
        self.assertEqual(self.logins(), [])
        self.assertEqual(Path(calls).read_text(), "", "no email: the Keychain is never asked")
        self.stop()
        self.log = os.path.join(self.dir, "display2.log")
        Path(self.log).write_text("")
        sec, calls = self.fake_security(exit_code=44)
        self.start(extra=["--email", "dj@example.com"], FAKE_SIGNEDOUT="1", FAKE_SIGNEDOUT_LOOP="1", SINGWS_SECURITY_BIN=sec)
        self.send(op="retry_service")
        time.sleep(4.5)
        self.assertEqual(self.logins(), [])
        self.assertIn("find-generic-password", Path(calls).read_text(), "the Keychain was asked and had nothing")

    def end_the_song_and_collect_effects(self, world):
        self.start()
        self.send(**dict(self.SONG))
        self.wait_for(lambda e: e.get("ev") == "done" and e.get("seq") == 1)
        self.wait_for(lambda e: e.get("ev") == "effect" and e["effect"][0] == "start_frames", timeout=20)
        if world is not None:
            self.send(op="world", **world)
        self.send(op="display", command={"cmd": "seekend"})          # the fake player now reports the song at its end
        effects, end = [], time.time() + 20
        while time.time() < end:
            try:
                ev = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev.get("ev") == "effect":
                effects.append(ev["effect"][0])
                if ev["effect"][0] == "finish_complete":
                    break
        return effects

    def test_the_end_of_the_song_completes_it_and_a_world_without_active_same_means_still_the_same(self):
        self.assertIn("finish_complete", self.end_the_song_and_collect_effects({"entry_current": True}))

    def test_the_end_of_the_song_is_not_completed_when_the_app_says_it_moved_on(self):
        self.assertNotIn("finish_complete", self.end_the_song_and_collect_effects({"entry_current": True, "active_same": False}))

    def test_a_song_without_an_enabled_field_is_accepted(self):
        self.start()
        song = {k: v for k, v in self.SONG.items() if k != "enabled"}
        self.send(**song)
        done = self.wait_for(lambda e: e.get("ev") == "done" and e["seq"] == 1)
        self.assertIs(done["ok"], True)


if __name__ == "__main__":
    unittest.main()
