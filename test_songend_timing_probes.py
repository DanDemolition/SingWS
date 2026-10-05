"""The song-end steps are timed so a show log can say which one causes the ~450 ms hitch seen on the Intel show Mac
(2026-10-03). The probes log only; they must stay on those steps and must not change what the methods return."""
import ast
import re
import time
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
LABELS = {
    "_handle_media_end_safe": "ui_songend_handler", "_finish_media_end_cleanup": "ui_songend_cleanup",
    "_apply_idle_background": "ui_songend_idle_background", "_mark_next_up_overlay_pending_after_completion": "ui_songend_outro",
    "_refresh_rotation_request_qr": "ui_songend_qr_rotation", "_refresh_show_screen_qr": "ui_songend_qr_show",
    "_recreate_video_surfaces": "ui_songend_video_surfaces", "clear_now_singing": "ui_songend_clear_now_singing",
    "update_bg_button_state": "ui_songend_bg_button", "stop_playback": "ui_songend_stop_playback",
    # finer song-end steps (cleanup and stop_playback), added after the 2026-10-04 show showed cleanup = 148 ms, stop_playback = 98 ms
    "_commit_pending_performance": "ui_songend_commit_performance", "_song_outro_payload_from_current": "ui_songend_outro_payload",
    "_flush_deferred_remote_adds": "ui_songend_flush_remote_adds", "_detach_video_sinks_now": "ui_songend_detach_sinks",
    "_show_idle_background_after_karaoke": "ui_songend_show_idle_bg", "_schedule_bg_resume": "ui_songend_schedule_bg_resume",
    "_gst_teardown_async": "ui_songend_teardown_async", "_remember_last_sung_from_current": "ui_songend_remember_last_sung",
    "_discard_pending_performance": "ui_songend_discard_performance", "_mark_daw_preview_playback_stopped": "ui_songend_daw_preview_stopped",
    "_reset_karaoke_tempo_for_track_end": "ui_songend_reset_tempo", "_reset_karaoke_key_for_track_end": "ui_songend_reset_key",
    "_update_deferred_remote_add_status": "ui_songend_remote_add_status",
    # song start (134 freezes, median 174 ms, about 3 per song start on the Intel Mac)
    "play_next_file": "ui_songstart_play_next", "_start_mpv_karaoke_transport": "ui_songstart_transport_setup",
    "_prepare_bg_for_karaoke_start": "ui_songstart_prepare_bg", "_prepare_karaoke_start": "ui_songstart_prepare",
    "_start_lyrics_background_video": "ui_songstart_bg_video", "_setup_end_silence_state": "ui_songstart_end_silence",
    "_push_mpv_audio_processing": "ui_songstart_audio_chain", "_arm_audio_end_floor": "ui_songstart_audio_end_floor",
    "_arm_visual_end_floor": "ui_songstart_visual_end_floor", "_trigger_show_screen_singer_start_vfx": "ui_songstart_singer_vfx",
    "_mark_daw_preview_playback_started": "ui_songstart_daw_preview", "_prescan_next_track": "ui_songstart_prescan_next",
    "_update_last_sung_card": "ui_last_sung_card",
    # inside the idle-background restore (44 ms, ~3 calls per song end on Intel): which part costs it? (Qt already caches decoded images)
    "_resolve_idle_background_path": "ui_songend_idle_bg_resolve", "set_background_image": "ui_songend_set_background_image",
}


class SongEndTimingProbeTests(unittest.TestCase):
    def test_every_song_end_step_is_timed(self):
        for name, label in LABELS.items():
            self.assertRegex(SOURCE, r'@_perf_timed\("%s"\)\n    def %s\(' % (re.escape(label), re.escape(name)), name)

    def test_the_wrapper_returns_the_value_and_reraises(self):
        tree = ast.parse(SOURCE)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_perf_timed")
        logged = []
        ns = {"time": time, "_perf_log_if_slow": lambda name, ms: logged.append((name, ms))}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "probe", "exec"), ns)

        @ns["_perf_timed"]("ui_x")
        def ok(a, b=1):
            return a + b

        @ns["_perf_timed"]("ui_y")
        def boom():
            raise ValueError("kept")
        self.assertEqual(ok(2, b=3), 5)
        with self.assertRaises(ValueError):
            boom()
        self.assertEqual([n for n, _ in logged], ["ui_x", "ui_y"])        # logged even when the step raised


    def test_the_native_load_call_is_timed(self):
        self.assertIn('transport.start(start_seconds)\n            _perf_log_if_slow("ui_songstart_native_load"', SOURCE)


class SongEndCollectionTests(unittest.TestCase):
    """The song-end handler used to run a FULL gc.collect() every time: about 200 ms of the 356 ms handler on the Intel show Mac."""

    def handler(self):
        start = SOURCE.index("    def _handle_media_end_safe(")
        return SOURCE[start:SOURCE.index("    def _auto_play_next_if_generation(")]

    def test_a_song_end_collects_only_the_young_generations_and_a_full_pass_at_most_every_thirty_minutes(self):
        h = self.handler()
        self.assertIn("_gc.collect(1)", h)
        self.assertIn(">= 1800.0", h)
        self.assertEqual(h.count("_gc.collect()"), 1)                       # the single full pass, behind the 30-minute rule
        self.assertLess(h.index(">= 1800.0"), h.index("_gc.collect()"))

    def test_the_first_song_end_of_a_session_does_not_run_a_full_pass(self):
        h = self.handler()
        self.assertIn('_gc_last_full = getattr(self, "_last_full_gc_ts", None)', h)
        self.assertIn("if _gc_last_full is None:\n                self._last_full_gc_ts = _gc_now", h)

    def test_the_collection_time_is_always_logged(self):
        self.assertIn("[PERF-DIAG] ui_songend_gc_", self.handler())


if __name__ == "__main__":
    unittest.main()
