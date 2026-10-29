"""`bg.next`, `bg.play_track` and `eq.set` (the Rust shell editing the background music and the equalisers) against the app's own code paths.

Real code under test: `_execute_shell_queue_command` (modes, request ids, argument checks), `shell_bg_next`, `shell_bg_play_track`, `shell_eq_set`, the real `GraphicEQ`
(`_ensure_eq_engines`) and the shared `_eq_refresh_live_attachment` that the EQ dialog now calls too. Recorded stand-ins: the background player, the BASS engine, the
mpv audio chain push and `save_settings`.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import time
import types
import unittest
from pathlib import Path

import test_shell_queue_commands as base


class FakePlayer:
    def __init__(self, playlist, index=0, playing=False):
        self.playlist, self.current_index, self.is_playing, self.calls = playlist, index, playing, []
        self._bgm_engine = types.SimpleNamespace(set_eq=lambda eq: self.calls.append(("set_eq", eq)))

    def next_track(self):
        self.calls.append("next_track")

    def stop(self):
        self.calls.append("stop")

    def play(self):
        self.calls.append(("play", self.current_index))


def harness(playlist=("/m/A.mp3", "/m/B.flac", "/m/C.m4a"), index=0, mode="apply", karaoke=False, simple=False):
    h = base.Harness([])
    a = h.app
    a.settings.update({"shell_commands": mode, "simple_audio_mode": simple, "eq_bgm": [], "eq_karaoke": []})
    a.karaoke_playing = karaoke
    a.karaoke_eq = None  # the app starts with no equaliser objects; on a bare (never initialised) Qt object a missing attribute would raise instead of defaulting
    a.bgm_eq = None
    a.bg_music = FakePlayer(list(playlist), index)
    a.update_bg_button_state = lambda: h.record("bg_button")
    a.update_bg_track_display = lambda: h.record("bg_display")
    a.highlight_current_track = lambda: h.record("highlight")
    a.save_settings = lambda: h.record("save_settings")
    a._push_mpv_audio_processing = lambda reason="update", audio_path="": h.record("mpv_push", reason)
    a._log_bgm_eq_route = lambda reason: h.record("eq_route", reason)
    return h


def with_engines(h):
    """Build the real equalisers now (the app's own setup attaches the BGM one to the player once) and forget those calls, so a test sees only what the command does."""
    h.app._ensure_eq_engines()
    h.app.bg_music.calls.clear()
    h.calls.clear()
    return h


def run(h, action, args, rid):
    a = dict(args, _request_id=rid, _sent_at=time.time())
    return h.app._execute_shell_queue_command(action, a)


class BackgroundMusicCommandTests(unittest.TestCase):
    def test_next_skips_the_track_the_way_the_apps_own_button_does(self):
        h = harness()
        self.assertEqual(run(h, "bg.next", {}, "n1"), (True, "skipped"))
        self.assertEqual(h.app.bg_music.calls, ["next_track"])
        self.assertIn(("bg_button",), h.calls)
        self.assertIn(("bg_display",), h.calls)

    def test_next_is_refused_while_karaoke_plays_or_without_a_playlist_and_a_dry_run_does_nothing(self):
        self.assertEqual(run(harness(karaoke=True), "bg.next", {}, "n2"), (False, "karaoke_playing"))
        self.assertEqual(run(harness(playlist=()), "bg.next", {}, "n3"), (False, "no_playlist"))
        h = harness(mode="dry_run")
        self.assertEqual(run(h, "bg.next", {}, "n4"), (True, "dry_run: would skip to the next track"))
        self.assertEqual(h.app.bg_music.calls, [])
        off = harness(mode="off")
        self.assertEqual(run(off, "bg.next", {}, "n5"), (False, "shell_commands_off"))
        self.assertEqual(off.app.bg_music.calls, [])

    def test_play_track_does_what_a_double_click_does_in_the_same_order(self):
        h = harness()
        self.assertEqual(run(h, "bg.play_track", {"index": 2, "track": "C.m4a"}, "p1"), (True, "playing"))
        self.assertEqual(h.app.bg_music.calls, ["stop", ("play", 2)], "the index is set BEFORE stop()/play(), like play_from_playlist")
        self.assertEqual(h.app.bg_music.current_index, 2)
        self.assertTrue(h.app._bgm_session_started)
        self.assertIn(("highlight",), h.calls)

    def test_a_stale_or_wrong_track_is_refused_and_nothing_starts(self):
        for args, code in (({"index": 1, "track": "A.mp3"}, "stale_playlist"), ({"index": 9, "track": "A.mp3"}, "unknown_track"), ({"index": 0, "track": "a.mp3"}, "stale_playlist")):
            h = harness()
            self.assertEqual(run(h, "bg.play_track", args, f"s{args['index']}"), (False, code), args)
            self.assertEqual(h.app.bg_music.calls, [])
        h = harness(karaoke=True)
        self.assertEqual(run(h, "bg.play_track", {"index": 0, "track": "A.mp3"}, "s5"), (False, "karaoke_playing"))
        self.assertEqual(run(harness(playlist=()), "bg.play_track", {"index": 0, "track": "A.mp3"}, "s6"), (False, "no_playlist"))
        d = harness(mode="dry_run")
        self.assertEqual(run(d, "bg.play_track", {"index": 1, "track": "B.flac"}, "s7"), (True, "dry_run: would play the track"))
        self.assertEqual(d.app.bg_music.calls, [])

    def test_the_track_name_may_carry_spaces_and_dict_entries_work(self):
        h = harness(playlist=({"path": "/m/Sub Dir/My Song.mp3", "filename": "My Song.mp3"},))
        self.assertEqual(run(h, "bg.play_track", {"index": 0, "track": " My Song.mp3 "}, "d1"), (True, "playing"))

    def test_bad_arguments_never_reach_the_player(self):
        h = harness()
        for n, (args, code) in enumerate((({"index": -1, "track": "A.mp3"}, "bad_index"), ({"index": True, "track": "A.mp3"}, "bad_index"), ({"index": 1.0, "track": "B.flac"}, "bad_index"),
                                          ({"index": "1", "track": "B.flac"}, "bad_index"), ({"track": "A.mp3"}, "bad_index"), ({"index": 0, "track": ""}, "bad_track"),
                                          ({"index": 0, "track": 5}, "bad_track"), ({"index": 0}, "bad_track"))):
            self.assertEqual(run(h, "bg.play_track", args, f"b{n}"), (False, code), args)
        self.assertEqual(h.app.bg_music.calls, [])

    def test_the_same_request_id_acts_once(self):
        h = harness()
        self.assertEqual(run(h, "bg.next", {}, "same"), (True, "skipped"))
        self.assertEqual(run(h, "bg.next", {}, "same"), (True, "duplicate: skipped"))
        self.assertEqual(h.app.bg_music.calls, ["next_track"])


class EqualiserCommandTests(unittest.TestCase):
    GAINS = [5, 4, 3, 1, -1, -1, 1, 3, 4, 5]

    def test_it_sets_the_gains_and_the_state_then_points_the_live_audio_at_them_then_saves(self):
        h = with_engines(harness())
        ok = run(h, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": True}, "e1")
        self.assertEqual(ok, (True, "changed"))
        eq = h.app.bgm_eq
        self.assertEqual(eq.gains_db(), [float(g) for g in self.GAINS])
        self.assertTrue(eq.enabled())
        self.assertEqual((h.app.settings["eq_bgm"], h.app.settings["eq_bgm_enabled"]), ([float(g) for g in self.GAINS], True))
        self.assertEqual(h.app.bg_music.calls, [("set_eq", eq)], "the BASS engine is handed the equaliser (it detaches it itself when off or flat)")
        self.assertIn(("eq_route", "shell_eq"), h.calls)
        self.assertEqual([c[0] for c in h.calls if c[0] in ("save_settings", "eq_route")], ["eq_route", "save_settings"], "live attachment first, then the save")
        self.assertEqual(h.app.settings["eq_karaoke"], [], "the other equaliser is untouched")

    def test_the_karaoke_equaliser_goes_through_the_mpv_audio_chain(self):
        h = with_engines(harness())
        self.assertEqual(run(h, "eq.set", {"target": "karaoke", "gains": [-3, -3, -2, -1, 1, 3, 4, 3, 2, 0], "enabled": True}, "e2"), (True, "changed"))
        self.assertEqual(h.app.karaoke_eq.gains_db()[:3], [-3.0, -3.0, -2.0])
        self.assertIn(("mpv_push", "shell_eq"), h.calls)
        self.assertEqual(h.app.bg_music.calls, [], "the music player is not involved")

    def test_gains_are_rounded_to_tenths_like_the_dialogs_sliders_and_the_limits_are_inclusive(self):
        h = harness()
        run(h, "eq.set", {"target": "bgm", "gains": [3.14, 0.04, -0.06, 12, -12, 11.95, 0.05, 1e-9, 6.25, -6.25], "enabled": True}, "e3")
        self.assertEqual(h.app.bgm_eq.gains_db(), [3.1, 0.0, -0.1, 12.0, -12.0, 11.9, 0.1, 0.0, 6.2, -6.2])

    def test_disabled_stays_disabled_even_with_gains_and_an_all_flat_set_can_be_enabled(self):
        h = harness()
        run(h, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": False}, "e4")
        self.assertFalse(h.app.bgm_eq.enabled())
        self.assertFalse(h.app.settings["eq_bgm_enabled"])
        run(h, "eq.set", {"target": "bgm", "gains": [0] * 10, "enabled": True}, "e5")
        self.assertTrue(h.app.bgm_eq.enabled())

    def test_bad_arguments_change_nothing(self):
        h = harness()
        good = {"target": "bgm", "gains": self.GAINS, "enabled": True}
        for n, (patch, code) in enumerate((({"target": "master"}, "bad_target"), ({"target": None}, "bad_target"), ({"gains": self.GAINS[:9]}, "bad_gains"),
                                           ({"gains": self.GAINS + [0]}, "bad_gains"), ({"gains": "rock"}, "bad_gains"), ({"gains": [12.1] + [0] * 9}, "bad_gains"),
                                           ({"gains": [-12.01] + [0] * 9}, "bad_gains"), ({"gains": [True] + [0] * 9}, "bad_gains"), ({"gains": [None] + [0] * 9}, "bad_gains"),
                                           ({"gains": ["3"] + [0] * 9}, "bad_gains"), ({"gains": [float("nan")] + [0] * 9}, "bad_gains"), ({"gains": [float("inf")] + [0] * 9}, "bad_gains"),
                                           ({"enabled": 1}, "bad_enabled"), ({"enabled": None}, "bad_enabled"), ({"enabled": "true"}, "bad_enabled"))):
            self.assertEqual(run(h, "eq.set", dict(good, **patch), f"x{n}"), (False, code), patch)
        self.assertEqual(h.app.settings["eq_bgm"], [])
        self.assertFalse(any(c[0] in ("save_settings", "mpv_push", "eq_route") for c in h.calls))
        self.assertIsNone(getattr(h.app, "bgm_eq", None), "the engines are not even built for a malformed command")

    def test_simple_audio_mode_refuses_like_the_dialog_and_a_dry_run_changes_nothing(self):
        s = harness(simple=True)
        self.assertEqual(run(s, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": True}, "m1"), (False, "simple_audio"))
        self.assertEqual(s.app.settings["eq_bgm"], [])
        d = harness(mode="dry_run")
        self.assertEqual(run(d, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": True}, "m2"), (True, "dry_run: would set the equaliser"))
        self.assertEqual((d.app.settings["eq_bgm"], d.app.bgm_eq.gains_db()), ([], [0.0] * 10))
        self.assertFalse(any(c[0] == "save_settings" for c in d.calls))
        off = harness(mode="off")
        self.assertEqual(run(off, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": True}, "m3"), (False, "shell_commands_off"))

    def test_the_shared_attachment_matches_what_the_dialog_does_in_simple_audio_mode_and_for_other_keys(self):
        h = with_engines(harness(simple=True))
        h.app._eq_refresh_live_attachment("eq_bgm", h.app.bgm_eq, "eq_dialog_change")
        self.assertEqual(h.app.bg_music.calls, [("set_eq", None)], "Simple Audio Mode keeps the BGM equaliser out of the chain")
        h.calls.clear()
        h.app._eq_refresh_live_attachment("something_else", h.app.bgm_eq)
        self.assertEqual(h.calls, [])

    def test_the_eq_dialog_uses_the_same_helper(self):
        src = (Path(__file__).with_name("0.2.18.1.py")).read_text()
        self.assertIn('self._eq_refresh_live_attachment(settings_key, eq, "eq_dialog_change")', src)
        self.assertEqual(src.count("bass.set_eq(None if self._simple_audio_mode() else eq)"), 1, "the attachment logic lives in exactly one place")

    def test_a_failure_inside_is_reported_not_raised(self):
        h = harness()
        h.app._ensure_eq_engines = lambda: (_ for _ in ()).throw(RuntimeError("scipy missing"))
        self.assertEqual(run(h, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": True}, "f1"), (False, "failed: scipy missing"))
        h2 = harness()
        h2.app._ensure_eq_engines = lambda: False
        self.assertEqual(run(h2, "eq.set", {"target": "bgm", "gains": self.GAINS, "enabled": True}, "f2"), (False, "eq_unavailable"))


class RelayEntryPointTests(unittest.TestCase):
    """The commands arrive the way the phone and the shell send them: a `command` message to `_on_host_relay_command`, answered with an ack."""

    def relay(self, h, command, args, rid):
        sent = []
        h.app.host_relay_worker = types.SimpleNamespace(send_json=sent.append, is_connected=lambda: True)
        h.app._publish_host_state_ws = lambda force=False: None
        h.app._on_host_relay_command({"type": "command", "command": command, "args": args, "request_id": rid, "sent_at": int(time.time())})
        return sent

    def test_next_play_track_and_eq_set_are_routed_and_acked(self):
        h = harness()
        self.assertEqual(self.relay(h, "bg.next", {"source": "web"}, "r1"), [{"type": "ack", "request_id": "r1", "ok": True, "message": "skipped"}])
        self.assertEqual(self.relay(h, "bg.play_track", {"index": 1, "track": "B.flac"}, "r2"), [{"type": "ack", "request_id": "r2", "ok": True, "message": "playing"}])
        got = self.relay(h, "eq.set", {"target": "bgm", "gains": [1] * 10, "enabled": True}, "r3")
        self.assertEqual(got, [{"type": "ack", "request_id": "r3", "ok": True, "message": "changed"}])
        self.assertEqual(h.app.bgm_eq.gains_db(), [1.0] * 10)
        off = harness(mode="off")
        self.assertEqual(self.relay(off, "bg.next", {}, "r4")[0]["message"], "shell_commands_off")
        self.assertEqual(self.relay(off, "eq.set", {"target": "bgm", "gains": [1] * 10, "enabled": True}, "r5")[0]["message"], "shell_commands_off")

    def test_the_polling_path_also_routes_these_prefixes(self):
        src = (Path(__file__).with_name("0.2.18.1.py")).read_text()
        self.assertEqual(src.count('action.startswith(("queue.", "library.", "bg.", "eq.", "waitlist.", "history.", "settings."))'), 3, "relay injection, polling injection and the executor routing")


if __name__ == "__main__":
    unittest.main()
