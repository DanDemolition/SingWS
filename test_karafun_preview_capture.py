"""Capturing the preview pane built into KaraFun's main window (opt-in), with the video window as the default and fallback."""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def namespace():
    tree = ast.parse(SOURCE)
    names = {"KARAFUN_PREVIEW_OVERLAY_MARGIN", "KARAFUN_PREVIEW_FRAME_TIMEOUT_S", "KARAFUN_PREVIEW_FIND_TIMEOUT_S"}
    funcs = {"karafun_preview_probe_script", "karafun_preview_pane_rect", "karafun_preview_region_changed"}
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


class WiringTests(unittest.TestCase):
    def test_default_is_the_video_window_and_the_switch_stores_text(self):
        self.assertIn('"karafun_capture_source": "video_window"', SOURCE)
        self.assertIn('self.settings["karafun_capture_source"] = "preview_pane" if checked else "video_window"', SOURCE)
        self.assertIn("karafun_preview_cb.toggled.connect(_on_karafun_preview_toggled)", SOURCE)

    def test_the_preview_source_is_chosen_only_when_asked_and_not_already_failed(self):
        body = SOURCE[SOURCE.index("def _start_karafun_dual_renderer_capture(self):"):]
        body = body[:body.index("lines = [")]
        self.assertIn('self._karafun_capture_source() == "preview_pane"', body)
        self.assertIn('_active_for_source.get("preview_capture_failed")', body)
        self.assertIn("self._start_karafun_preview_capture(token)", body)

    def test_a_failure_falls_back_to_the_video_window_for_that_song(self):
        body = SOURCE[SOURCE.index("def _fallback_karafun_preview_to_video_window("):SOURCE.index("def _refresh_karafun_preview_region(")]
        self.assertIn('active["preview_capture_failed"] = True', body)
        self.assertIn("self._stop_karafun_dual_renderer_capture()", body)
        self.assertIn("self._start_karafun_dual_renderer_capture", body)

    def test_every_way_the_preview_can_fail_reaches_the_fallback(self):
        find = SOURCE[SOURCE.index("def _start_karafun_preview_capture("):SOURCE.index("def _fallback_karafun_preview_to_video_window(")]
        self.assertIn("self._fallback_karafun_preview_to_video_window(reason)", find)             # pane never found
        stream = SOURCE[SOURCE.index("def _begin_karafun_capture_stream("):SOURCE.index("def _stop_karafun_dual_renderer_capture(")]
        self.assertIn('f"capture did not start: {exc}"', stream)                                    # stream would not start
        self.assertIn("self._fallback_karafun_preview_to_video_window(str(exc))", stream)          # status / permission error
        self.assertIn("self._check_karafun_preview_alive()", stream)                               # silence while playing

    def test_silence_while_paused_is_not_a_failure(self):
        body = SOURCE[SOURCE.index("def _check_karafun_preview_alive("):SOURCE.index("def _begin_karafun_capture_stream(")]
        self.assertIn('entry.get("karafun_paused_at")', body)
        self.assertIn("KARAFUN_PREVIEW_FRAME_TIMEOUT_S", body)

    def test_the_picture_is_fitted_not_stretched_in_preview_mode(self):
        self.assertIn("set_karaoke_frame(frame, stretch_fill=(region is None))", SOURCE)

    def test_the_region_follows_the_panel_and_state_resets(self):
        stream = SOURCE[SOURCE.index("def _begin_karafun_capture_stream("):SOURCE.index("def _stop_karafun_dual_renderer_capture(")]
        self.assertIn("self._refresh_karafun_preview_region()", stream)
        stop = SOURCE[SOURCE.index("def _stop_karafun_dual_renderer_capture("):][:900]
        for key in ("_karafun_preview_region", "_karafun_capture_started_at", "_karafun_preview_probe_inflight"):
            self.assertIn(key, stop)

    def test_region_probing_never_runs_on_the_gui_thread(self):
        for name in ("_start_karafun_preview_capture", "_refresh_karafun_preview_region"):
            body = SOURCE[SOURCE.index(f"def {name}("):]
            body = body[:body.index("\n    def ", 10)]
            self.assertIn("threading.Thread(", body, name)
            self.assertIn("_run_karafun_applescript_sync(karafun_preview_probe_script()", body.replace("\n", " ").replace("  ", " "), name)


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
