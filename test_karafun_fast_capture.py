"""The KaraFun picture starts from the last song's pane region while the same probe checks it (2026-10-03: the first
frame no longer waits ~2 s for the Accessibility probe), and the cheaper probe still finds the pane."""
import ast
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
WANT_FUNCS = {"karafun_preview_probe_script", "karafun_preview_pane_rect", "karafun_preview_region_changed", "karafun_fill_region"}
WANT_NAMES = {"KARAFUN_PREVIEW_OVERLAY_MARGIN", "KARAFUN_PREVIEW_FIND_TIMEOUT_S", "KARAFUN_FILL_ZOOM_OUT"}

from test_karafun_preview_capture import REAL   # what System Events really reported for KaraFun's main window


def namespace():
    tree = ast.parse(SOURCE)
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in WANT_FUNCS)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in WANT_NAMES for t in n.targets))]
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_start_karafun_preview_capture":
                    sub.decorator_list = []; method = sub
                if isinstance(sub, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KARAFUN_REGION_CACHE_MAX_AGE_S" for t in sub.targets):
                    body.append(sub)
    ns = {"time": time, "threading": threading, "_diag": lambda *a: None, }
    consts = [n for n in body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in WANT_NAMES for t in n.targets)]
    exec(compile(ast.Module(body=consts + [n for n in body if isinstance(n, ast.FunctionDef)] + [method], type_ignores=[]), "fast-capture", "exec"), ns)
    return ns


NS = namespace()


def host(cache=None, age=0.0, probe_out=REAL):
    log = []
    h = SimpleNamespace(KARAFUN_REGION_CACHE_MAX_AGE_S=6 * 3600, _karafun_handoff_token="tok", _karafun_capture_active=True)
    state = h.__dict__
    if cache is not None:
        state["_karafun_last_preview_region"] = cache
        state["_karafun_last_preview_region_at"] = time.monotonic() - age
    h._run_on_ui_thread = lambda fn: fn()
    h._show_processing_notification = lambda *a, **k: None

    def probe(script, timeout=8):
        log.append("probe"); time.sleep(0.05); return True, probe_out, ""
    h._run_karafun_applescript_sync = probe
    capture = mock.Mock()

    def begin(token, region=None):
        log.append(("begin", region)); h._karafun_capture = capture; state["_karafun_preview_region"] = region
    h._begin_karafun_capture_stream = begin
    h._start = lambda token: NS["_start_karafun_preview_capture"](h, token)
    return h, log, capture


def wait_for(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond(): return True
        time.sleep(0.02)
    return False


class FastCaptureTests(unittest.TestCase):
    def test_first_song_of_the_session_waits_for_the_probe(self):
        h, log, capture = host()
        h._start("tok"); self.assertTrue(wait_for(lambda: any(isinstance(e, tuple) for e in log)))
        self.assertEqual(log[0], "probe")                                    # nothing starts before the pane is found
        self.assertEqual(sum(1 for e in log if isinstance(e, tuple)), 1)
        self.assertIsNone(h.__dict__["_karafun_frame_gate_until"])
        self.assertIsNotNone(h.__dict__["_karafun_last_preview_region"])      # remembered for the next song

    def test_next_song_starts_at_once_from_the_remembered_region(self):
        first, _, _ = host(); first._start("tok"); wait_for(lambda: "_karafun_last_preview_region" in first.__dict__ and first.__dict__.get("_karafun_last_preview_region_at"))
        remembered = first.__dict__["_karafun_last_preview_region"]
        h, log, capture = host(cache=remembered)
        h._start("tok")
        self.assertTrue(wait_for(lambda: log and isinstance(log[0], tuple)))
        self.assertEqual(log[0], ("begin", remembered))                       # picture started BEFORE the first probe finished
        self.assertTrue(wait_for(lambda: "probe" in log))
        time.sleep(0.3)
        self.assertEqual(sum(1 for e in log if isinstance(e, tuple)), 1)      # never started twice
        capture.set_region.assert_not_called()                                # the pane had not moved
        self.assertGreater(h.__dict__["_karafun_frame_gate_until"] or 1, 0)

    def test_a_moved_pane_is_re_aimed(self):
        h, log, capture = host(cache=(100.0, 50.0, 400.0, 225.0))
        h._start("tok")
        self.assertTrue(wait_for(lambda: capture.set_region.called))
        self.assertEqual(sum(1 for e in log if isinstance(e, tuple)), 1)
        fitted = capture.set_region.call_args[0][0]
        self.assertNotEqual(tuple(round(v) for v in fitted), (100, 50, 400, 225))

    def test_an_old_remembered_region_is_not_trusted(self):
        h, log, capture = host(cache=(100.0, 50.0, 400.0, 225.0), age=7 * 3600)
        h._start("tok"); self.assertTrue(wait_for(lambda: any(isinstance(e, tuple) for e in log)))
        self.assertEqual(log[0], "probe")

    def test_a_song_that_ended_meanwhile_starts_nothing(self):
        h, log, capture = host(cache=(100.0, 50.0, 400.0, 225.0))
        h._karafun_capture_active = False
        h._start("tok"); time.sleep(0.4)
        self.assertEqual([e for e in log if isinstance(e, tuple)], [])


class SourceGuards(unittest.TestCase):
    def test_idle_player_frames_are_held_back_until_the_song_is_playing_but_never_forever(self):
        i = SOURCE.index("gate = _state.get(\"_karafun_frame_gate_until\")")
        body = SOURCE[i:i + 420]
        self.assertIn('!= "playing"', body)
        self.assertIn("seen_at < gate", body)
        self.assertIn("time.monotonic() + 6.0", SOURCE)

    def test_the_probe_no_longer_reads_element_names(self):
        i = SOURCE.index("def karafun_preview_probe_script")
        body = SOURCE[i:i + 2600]
        self.assertNotIn("my cleanText(name of h)", body)
        self.assertIn("'set nm to \"\"'", body)


if __name__ == "__main__":
    unittest.main()
