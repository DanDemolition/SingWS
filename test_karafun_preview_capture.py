"""Capturing the preview pane built into KaraFun's main window (opt-in), with the video window as the default and fallback."""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def namespace():
    tree = ast.parse(SOURCE)
    names = {"KARAFUN_PREVIEW_OVERLAY_MARGIN", "KARAFUN_PREVIEW_FRAME_TIMEOUT_S", "KARAFUN_PREVIEW_FIND_TIMEOUT_S",
             "KARAFUN_PREVIEW_MAX_RESTARTS"}
    funcs = {"karafun_preview_probe_script", "karafun_preview_pane_rect", "karafun_preview_region_changed", "karafun_fill_region"}
    body = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name in funcs)]
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "preview", "exec"), ns)
    return ns


NS = namespace()
pane = NS["karafun_preview_pane_rect"]

# What System Events reported for KaraFun's main window on 2026-10-02 (idle, nothing playing).
REAL = "\n".join([
    "WINDOW|0|34|1728|1013",
    "E|AXTextField||8|86|192|30",
    "E|AXSplitter||207|86|0|961",
    "E|AXButton||905|95|30|30",
    "E|AXSplitter||944|86|1|961",
    "E|AXImage||966|386|36|17",
    "E|AXStaticText|No item is being played|1008|384|162|21",
    "E|AXButton||962|421|30|30",
    "E|AXButton||998|421|30|30",
    "E|AXStaticText|00:00|1034|428|36|17",
    "E|AXSlider||1076|415|557|42",
    "E|AXStaticText|00:00|1639|428|36|17",
    "E|AXButton||1681|421|30|30",
    "E|AXPopUpButton||954|479|91|26",
    "E|AXStaticText||1053|485|2|16",
    "E|AXScrollArea||945|510|783|508",
    "E|AXStaticText|Your queue is empty.|1250|741|172|22",
])


class PaneRectTests(unittest.TestCase):
    def test_the_real_layout(self):
        found = pane(REAL)
        self.assertEqual(found["window"], (0.0, 34.0, 1728.0, 1013.0))
        # left = just right of the divider at 944; top = where the divider starts; right = the window edge;
        # bottom = 14 points above the highest overlay control (384)
        self.assertEqual(found["region"], (945.0, 52.0, 783.0, 284.0))

    def test_a_moved_window_gives_window_relative_numbers(self):
        dx, dy = 200, 66                                                   # the window moved right and down
        lines = []
        for line in REAL.splitlines():
            parts = line.split("|")
            if parts[0] == "WINDOW":
                parts[1], parts[2] = str(int(parts[1]) + dx), str(int(parts[2]) + dy)
            else:
                parts[-4], parts[-3] = str(int(parts[-4]) + dx), str(int(parts[-3]) + dy)
            lines.append("|".join(parts))
        found = pane("\n".join(lines))
        self.assertEqual(found["region"], (945.0, 52.0, 783.0, 284.0))        # same place inside the window
        self.assertEqual(found["window"], (200.0, 100.0, 1728.0, 1013.0))

    def test_a_wider_panel_is_followed(self):
        wide = REAL.replace("E|AXSplitter||944|86|1|961", "E|AXSplitter||700|86|1|961")
        left = pane(wide)["region"]
        self.assertEqual((left[0], left[2]), (701.0, 1027.0))

    def test_a_taller_overlay_start_moves_the_bottom(self):
        higher = REAL.replace("|966|386|", "|966|300|").replace("|1008|384|", "|1008|298|")
        self.assertEqual(pane(higher)["region"][3], 298 - 14 - 86)

    def test_no_overlay_controls_uses_the_queue_header(self):
        trimmed = "\n".join(l for l in REAL.splitlines() if not any(k in l for k in ("AXImage", "AXSlider", "|AXButton||962", "|AXButton||998",
                                                                                      "00:00", "No item", "|AXButton||1681")))
        self.assertEqual(pane(trimmed)["region"][3], 479 - 8 - 86)

    def test_unrecognised_layouts_return_none(self):
        self.assertIsNone(pane(""))
        self.assertIsNone(pane("NO_WINDOW"))
        self.assertIsNone(pane("NOT_RUNNING"))
        self.assertIsNone(pane("WINDOW|0|34|1728|1013"))
        no_splitter = "\n".join(l for l in REAL.splitlines() if "AXSplitter" not in l)
        self.assertIsNone(pane(no_splitter))
        self.assertIsNone(pane("WINDOW|0|34|300|300\nE|AXSplitter||200|86|1|400"))     # far too small a picture
        self.assertIsNone(pane("WINDOW|0|34|1728|1013\nE|AXSplitter||944|86|x|961\nnonsense|||"))

    def test_names_with_pipes_do_not_break_parsing(self):
        weird = REAL.replace("No item is being played", "A|B|C")
        # the probe strips pipes from names, but even if one slipped through the numbers are read from the end
        self.assertEqual(pane(weird)["region"], (945.0, 52.0, 783.0, 284.0))


class RegionChangeTests(unittest.TestCase):
    def test_small_jitter_is_ignored(self):
        changed = NS["karafun_preview_region_changed"]
        self.assertFalse(changed((945, 52, 783, 284), (946, 52.5, 783, 283)))
        self.assertTrue(changed((945, 52, 783, 284), (945, 52, 700, 284)))
        self.assertTrue(changed(None, (1, 2, 3, 4)))
        self.assertFalse(changed(None, None))


@unittest.skipUnless(sys.platform == "darwin", "AppleScript is macOS only")
class ProbeScriptTests(unittest.TestCase):
    def test_it_compiles_and_is_read_only(self):
        source = "\n".join(NS["karafun_preview_probe_script"]())
        result = subprocess.run(["osacompile", "-o", "/dev/null"], input=source, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        for forbidden in ("click", "perform action", "set position", "set value", "keystroke", "key code"):
            self.assertNotIn(forbidden, source)

    def test_it_skips_the_video_window_and_reports_the_main_one(self):
        source = "\n".join(NS["karafun_preview_probe_script"]())
        self.assertIn('windowName is not "Dual Renderer"', source)
        self.assertIn('"WINDOW|"', source)
        self.assertIn('role is "AXSplitGroup"', source)


class FillRegionTests(unittest.TestCase):
    """The pane is about 2.7:1; cut the sides to 16:9 so the TV is filled with no bars and no stretching."""

    def test_the_real_pane_is_cut_to_exactly_16_by_9_and_centred(self):
        fill = NS["karafun_fill_region"]
        x, y, w, h = fill((945.0, 52.0, 783.0, 284.0))
        self.assertAlmostEqual(w / h, 16 / 9, places=3)
        self.assertEqual((y, h), (52.0, 284.0))                       # full height kept
        self.assertAlmostEqual((x - 945.0), (945.0 + 783.0) - (x + w), places=3)     # same amount cut off each side
        self.assertGreater(w, 480); self.assertLess(w, 520)

    def test_a_region_that_is_already_16_by_9_or_narrower_is_unchanged(self):
        fill = NS["karafun_fill_region"]
        self.assertEqual(fill((0.0, 0.0, 1600.0, 900.0)), (0.0, 0.0, 1600.0, 900.0))
        self.assertEqual(fill((10.0, 20.0, 700.0, 500.0)), (10.0, 20.0, 700.0, 500.0))

    def test_bad_regions_pass_through(self):
        fill = NS["karafun_fill_region"]
        self.assertEqual(fill((0, 0, 0, 100)), (0, 0, 0, 100))
        self.assertEqual(fill((0, 0, 100, -5)), (0, 0, 100, -5))

    def test_a_taller_panel_cuts_less(self):
        fill = NS["karafun_fill_region"]
        small = fill((0.0, 0.0, 783.0, 284.0))[2]
        big = fill((0.0, 0.0, 783.0, 380.0))[2]
        self.assertGreater(big, small)                                # more of the width is kept when the panel is taller

    def test_it_is_used_for_the_first_capture_and_for_following_resizes(self):
        self.assertIn('region=karafun_fill_region(found["region"])', SOURCE)
        refresh = SOURCE[SOURCE.index("def _refresh_karafun_preview_region("):SOURCE.index("def _check_karafun_preview_alive(")]
        self.assertIn('fitted = karafun_fill_region(found["region"]) if found else None', refresh)
        self.assertIn("capture.set_region(fitted)", refresh)

    def test_the_audience_picture_fills_the_window(self):
        self.assertIn("vw.video_area.set_karaoke_frame(frame, stretch_fill=True)", SOURCE)


class SimplifiedSettingsTests(unittest.TestCase):
    """Settings > KaraFun > Playback is two switches; the video-window method and its extras are gone."""

    def card(self):
        i = SOURCE.index('v = _section_card(tab_karafun, "Playback",')
        return SOURCE[i:SOURCE.index('v = _section_card(tab_karafun, "Audio Output")')]

    def test_there_are_exactly_two_switches(self):
        card = self.card()
        self.assertEqual(card.count("QCheckBox("), 2)
        self.assertIn('QCheckBox("Use KaraFun integration")', card)
        self.assertIn('QCheckBox("Launch KaraFun when SingWS starts")', card)

    def test_the_master_switch_drives_search_start_key_tempo_and_capture(self):
        card = self.card()
        self.assertIn('self.settings["karafun_auto_queue_enabled"] = bool(checked)', card)
        self.assertIn('self.settings["karafun_auto_key_tempo"] = bool(checked)', card)
        self.assertIn('self.settings["karafun_dual_renderer_capture"] = True', card)

    def test_capture_is_forced_on_at_launch_whatever_was_saved(self):
        self.assertIn('self.settings["karafun_dual_renderer_capture"] = True', SOURCE[SOURCE.index('self.settings["karaoke_engine"] = "mpv"'):][:900])
        self.assertIn('"karafun_dual_renderer_capture": True', SOURCE)

    def test_launching_karafun_at_startup_needs_the_integration_on(self):
        body = SOURCE[SOURCE.index("def _launch_karafun_at_startup(self):"):][:900]
        self.assertIn('self.settings.get("karafun_launch_at_startup", True)', body)
        self.assertIn('self.settings.get("karafun_auto_queue_enabled", False)', body)

    def test_the_removed_settings_and_code_are_really_gone(self):
        for gone in ("karafun_park_video_window", "karafun_capture_source", "karafun_dual_renderer_windows",
                     "karafun_renderer_press_decision", "_ensure_renderer_windowed", "_park_karafun_video_window",
                     "_check_parked_video_alive", "_fallback_karafun_preview_to_video_window", "preview_capture_failed",
                     "karafun_move_window_script", "karafun_park_position", "Keep KaraFun's video window out of the way",
                     "Show KaraFun Dual Renderer inside SingWS"):
            self.assertNotIn(gone, SOURCE, gone)


class CaptureFlowTests(unittest.TestCase):
    def test_the_capture_always_goes_to_the_preview_pane(self):
        body = SOURCE[SOURCE.index("def _start_karafun_dual_renderer_capture(self):"):SOURCE.index("def _start_karafun_preview_capture(")]
        self.assertIn("self._start_karafun_preview_capture(token)", body)
        self.assertNotIn("Dual Renderer", body.replace('"""Start capturing', ""))        # no video-window branch left

    def test_a_missing_pane_keeps_being_looked_for_and_the_operator_is_told_once(self):
        body = SOURCE[SOURCE.index("def _start_karafun_preview_capture("):SOURCE.index("def _restart_karafun_preview_capture(")]
        self.assertIn("while True:", body)
        self.assertIn("not getattr(self, \"_karafun_capture_active\", False)", body)            # stops when the song ends
        self.assertIn("if not notified and", body)
        self.assertIn("KaraFun's picture isn't available yet", body)

    def test_every_way_the_picture_can_be_lost_restarts_the_capture(self):
        stream = SOURCE[SOURCE.index("def _begin_karafun_capture_stream("):SOURCE.index("def _stop_karafun_dual_renderer_capture(")]
        self.assertIn('self._restart_karafun_preview_capture(f"capture did not start: {exc}")', stream)
        self.assertIn("self._restart_karafun_preview_capture(str(exc))", stream)
        self.assertIn("self._check_karafun_preview_alive()", stream)
        alive = SOURCE[SOURCE.index("def _check_karafun_preview_alive("):SOURCE.index("def _begin_karafun_capture_stream(")]
        self.assertIn("self._restart_karafun_preview_capture(", alive)

    def test_silence_while_paused_is_not_a_failure(self):
        body = SOURCE[SOURCE.index("def _check_karafun_preview_alive("):SOURCE.index("def _begin_karafun_capture_stream(")]
        self.assertIn('entry.get("karafun_paused_at")', body)
        self.assertIn("KARAFUN_PREVIEW_FRAME_TIMEOUT_S", body)

    def test_the_region_follows_the_panel_and_state_resets(self):
        stream = SOURCE[SOURCE.index("def _begin_karafun_capture_stream("):SOURCE.index("def _stop_karafun_dual_renderer_capture(")]
        self.assertIn("self._refresh_karafun_preview_region()", stream)
        stop = SOURCE[SOURCE.index("def _stop_karafun_dual_renderer_capture("):][:900]
        for key in ("_karafun_preview_region", "_karafun_capture_started_at", "_karafun_preview_probe_inflight", "_karafun_last_frame_at"):
            self.assertIn(key, stop)

    def test_probing_never_runs_on_the_gui_thread(self):
        for name in ("_start_karafun_preview_capture", "_refresh_karafun_preview_region"):
            body = SOURCE[SOURCE.index(f"def {name}("):]
            body = body[:body.index("\n    def ", 10)]
            self.assertIn("threading.Thread(", body, name)
            self.assertIn("_run_karafun_applescript_sync(karafun_preview_probe_script()", body.replace("\n", " ").replace("  ", " "), name)


class RestartTests(unittest.TestCase):
    """_restart_karafun_preview_capture: a few automatic restarts per song, then a plain message."""

    def build(self):
        tree = ast.parse(SOURCE)
        method = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) and sub.name == "_restart_karafun_preview_capture":
                        method = sub
        ns = dict(NS)
        timers = []
        ns.update(_diag=lambda *a: None, QTimer=type("T", (), {"singleShot": staticmethod(lambda ms, fn: timers.append((ms, fn)))}))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "restart", "exec"), ns)
        from types import SimpleNamespace
        active = {"entry": {}}
        host = SimpleNamespace(_active_external_karafun=active, stopped=0, shown=[], started=0)
        host._stop_karafun_dual_renderer_capture = lambda: setattr(host, "stopped", host.stopped + 1)
        host._show_processing_notification = lambda msg, level="info": host.shown.append((msg, level))
        host._start_karafun_dual_renderer_capture = lambda: setattr(host, "started", host.started + 1)
        return ns["_restart_karafun_preview_capture"], host, active, timers

    def test_restarts_a_few_times_then_gives_up_with_a_message(self):
        restart, host, active, timers = self.build()
        limit = NS["KARAFUN_PREVIEW_MAX_RESTARTS"]
        for n in range(limit):
            restart(host, "lost")
            ms, fn = timers[-1]
            self.assertGreaterEqual(ms, 1000)
            fn()
            self.assertEqual(host.started, n + 1)
        self.assertEqual(active["preview_restarts"], limit)
        restart(host, "lost again")
        self.assertEqual(len(timers), limit)                           # no further restart scheduled
        self.assertEqual(host.shown[-1][1], "error")
        self.assertIn("picture was lost", host.shown[-1][0])

    def test_the_first_loss_says_it_is_reconnecting(self):
        restart, host, _active, _timers = self.build()
        restart(host, "lost")
        self.assertEqual(host.shown[0][1], "warning")
        self.assertIn("reconnecting", host.shown[0][0])

    def test_a_restart_for_a_song_that_has_ended_does_nothing(self):
        restart, host, _active, timers = self.build()
        restart(host, "lost")
        host._active_external_karafun = None                           # the song ended during the 2.5 s pause
        timers[-1][1]()
        self.assertEqual(host.started, 0)

    def test_with_no_active_song_it_stops_and_does_not_restart(self):
        restart, host, _active, timers = self.build()
        host._active_external_karafun = None
        restart(host, "lost")
        self.assertEqual(timers, [])
        self.assertEqual(host.stopped, 1)


@unittest.skipUnless(sys.platform == "darwin", "the capture library is macOS only")
class WrapperTests(unittest.TestCase):
    def test_the_library_exports_the_region_calls_for_both_architectures(self):
        for arch in ("arm64", "x86_64"):
            lib = Path("native/karafun_capture") / f"libsingws_karafun_capture-{arch}.dylib"
            if not lib.exists():
                self.skipTest(f"{lib} not built")
            out = subprocess.run(["nm", "-gU", str(lib)], capture_output=True, text=True).stdout
            self.assertIn("_singws_karafun_capture_start_region", out, arch)
            self.assertIn("_singws_karafun_capture_set_region", out, arch)

    def test_the_wrapper_reports_region_support_on_this_machine(self):
        import platform
        lib = Path("native/karafun_capture") / f"libsingws_karafun_capture-{'arm64' if platform.machine() == 'arm64' else 'x86_64'}.dylib"
        if not lib.exists():
            self.skipTest("library not built")
        from karafun_capture import KaraFunCapture
        capture = KaraFunCapture(library_path=lib)
        self.assertTrue(capture.supports_region)          # never start a capture here: that would ask for Screen Recording


if __name__ == "__main__":
    unittest.main()
