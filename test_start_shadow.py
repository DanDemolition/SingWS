"""The song-start routing shadow (start_shadow.py + Rust singws-start).

DIFFERENTIAL: a transcription of the app's own chain in `play_next_file` (evaluation order, `.lower()` for the ZIP preparation check but case-sensitive `.endswith` in
the dispatch, tuple unpacking that raises, `str()` of a non-text first item, a `song_info` that is not text or a sequence) is run on real Python values and must give
the same route as the Rust library for every combination of facts. Plus the shadow's own behaviour (decision/mismatch/summary lines, skips, never raises).
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import itertools
import os
import unittest
from pathlib import Path

import start_shadow

# the dev build of the library, like the other shadow tests (SINGWS_DSP_LIB overrides)
os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


def oracle(song_info, external, has_primary, primary_exists, zip_cached, mp3g, pair):
    """The route `play_next_file` takes, as plain Python on real values."""
    if external:
        return "external_karafun"
    if has_primary and not primary_exists:
        return "reject_missing_file"
    if isinstance(song_info, str) and song_info.lower().endswith(".zip"):
        if not zip_cached:
            return "zip_prepare"
    try:
        if isinstance(song_info, (tuple, list)):
            if isinstance(song_info[0], str) and song_info[0].endswith(".cdg"):
                cdg_path, mp3_path = song_info  # raises unless exactly two
                return "cdg_mp3_dual"
            elif isinstance(song_info[0], str) and song_info[0].endswith(".mp4"):
                return "mp4"
            shown = str(song_info[0])
            if shown.endswith(".mp4"):
                return "mp4"
            elif shown.endswith(".mp3"):
                return "mp3"
            return "reject_unsupported_tuple"
        if song_info.endswith(".mp4"):
            return "mp4"
        elif song_info.endswith(".mp3"):
            return "mp3"
        elif song_info.endswith(".zip"):
            return "mp3g_zip" if mp3g else "reject_invalid_zip"
        elif song_info.endswith(".cdg"):
            return "cdg_mp3_dual" if pair else "reject_missing_cdg_audio_pair"
        return "reject_unsupported_format"
    except Exception:
        return "error"


class Odd:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text


INFOS = [
    "/l/a.mp4", "/l/a.mp3", "/l/a.zip", "/l/A.ZIP", "/l/a.Zip", "/l/a.cdg", "/l/a.CDG", "/l/a.MP4", "/l/a.txt", "/l/a", "", ".zip", ".cdg", "/l/a.mp4 ", "/l/é.mp3",
    ("/l/a.cdg", "/l/a.mp3"), ["/l/a.cdg", "/l/a.mp3"], ("/l/a.cdg",), ("/l/a.cdg", "/l/a.mp3", "x"), ("/l/a.mp4", "/l/b.mp4"), ("/l/a.mp4",), ("/l/a.mp3", "x"),
    ("/l/a.zip", "x"), ("/l/a.CDG", "/l/a.mp3"), (), [], (None, "x"), (Odd("/odd/a.mp4"), "y"), (Odd("/odd/a.cdg"), "y"), (Odd("/odd/a.mp3"),), (5, 6), ("",),
    None, 5, {"a": 1}, 3.5,
]
TRI = [False, True]


class RustAgreesWithTheAppsChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shadow = start_shadow.StartShadow(lambda s: None)
        if not cls.shadow.available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built (rust/build_dsp.sh)")

    def test_every_combination_of_facts_gives_the_same_route(self):
        n = 0
        for info, ext, hp, pe, zc, mp, pr in itertools.product(INFOS, TRI, TRI, TRI, TRI, TRI, TRI):
            want = oracle(info, ext, hp, pe, zc, mp, pr)
            req = {"external": ext, "info": start_shadow.info_request(info), "has_primary": hp, "primary_exists": pe, "zip_cached": zc, "mp3g": mp, "pair": pr}
            got = self.shadow._call(req)
            self.assertEqual(got, want, (info, ext, hp, pe, zc, mp, pr))
            n += 1
        self.assertEqual(n, len(INFOS) * 64)

    def test_check_agrees_at_each_site_with_only_the_facts_known_there(self):
        sh = start_shadow.StartShadow(lambda s: None)
        # the chain commits to a route early, before later facts exist
        self.assertTrue(sh.check("external_karafun", "/l/a.mp4", external=True))
        self.assertTrue(sh.check("reject_missing_file", "/l/a.mp4", has_primary=True, primary_exists=False))
        self.assertTrue(sh.check("zip_prepare", "/l/a.zip", has_primary=True, primary_exists=True, zip_cached=False))
        self.assertTrue(sh.check("mp4", "/l/a.mp4", has_primary=True, primary_exists=True))
        self.assertTrue(sh.check("mp3g_zip", "/l/a.zip", has_primary=True, primary_exists=True, zip_cached=True, mp3g=True))
        self.assertTrue(sh.check("reject_invalid_zip", "/l/a.zip", has_primary=True, primary_exists=True, zip_cached=True, mp3g=False))
        self.assertTrue(sh.check("cdg_mp3_dual", "/l/a.cdg", has_primary=True, primary_exists=True, pair=True))
        self.assertTrue(sh.check("reject_missing_cdg_audio_pair", "/l/a.cdg", has_primary=True, primary_exists=True, pair=False))
        self.assertTrue(sh.check("reject_unsupported_format", "/l/a.txt", has_primary=True, primary_exists=True))
        self.assertTrue(sh.check("cdg_mp3_dual", ("/l/a.cdg", "/l/a.mp3"), has_primary=True, primary_exists=True))
        self.assertTrue(sh.check("reject_unsupported_tuple", ("/l/a.zip", "x"), has_primary=True, primary_exists=True))
        self.assertEqual(sh.mismatches, 0)

    def test_a_site_that_claims_the_wrong_route_or_hides_a_fact_is_a_mismatch(self):
        lines = []
        sh = start_shadow.StartShadow(lines.append)
        self.assertFalse(sh.check("mp3", "/l/a.mp4", has_primary=True, primary_exists=True))
        self.assertFalse(sh.check("cdg_mp3_dual", "/l/a.cdg", has_primary=True, primary_exists=True), "the pair fact was not passed: Rust answers need:pair")
        self.assertEqual(sh.mismatches, 2)
        self.assertTrue(any("MISMATCH app=mp3 rust=mp4" in x for x in lines), lines)
        self.assertTrue(any("need:pair" in x for x in lines), lines)


class ShadowBehaviour(unittest.TestCase):
    class FakeLib:
        def __init__(self, route):
            self.route = route
            self.requests = []

        def singws_start_json(self, payload, buf, cap):
            import json
            self.requests.append(json.loads(payload.decode()))
            out = json.dumps({"route": self.route}).encode()
            if self.route == "__fail__":
                return -3
            buf[: len(out)] = out
            return len(out)

    def test_info_request_describes_every_shape(self):
        self.assertEqual(start_shadow.info_request("/a.mp4"), {"str": "/a.mp4"})
        self.assertEqual(start_shadow.info_request(("/a.cdg", "/a.mp3")), {"seq": [{"s": "/a.cdg"}, {"s": "/a.mp3"}]})
        self.assertEqual(start_shadow.info_request([Odd("x.mp4"), "b"]), {"seq": [{"o": "x.mp4"}, {"s": "b"}]})
        self.assertEqual(len(start_shadow.info_request(("a", "b", "c", "d", "e"))["seq"]), 3, "only the first three items can matter")
        self.assertEqual(start_shadow.info_request(None), {"other": True})
        self.assertEqual(start_shadow.info_request({"a": 1}), {"other": True})
        long = "x" * 10000 + ".mp4"
        self.assertTrue(start_shadow.info_request(long)["str"].endswith(".mp4") and len(start_shadow.info_request(long)["str"]) == 4096, "a very long path keeps its tail")

        class Bad:
            def __str__(self):
                raise RuntimeError("boom")

        self.assertEqual(start_shadow.info_request((Bad(),)), {"seq": [{"o": ""}]}, "a str() that raises never escapes")

    def test_a_decision_line_appears_once_per_route_and_a_summary_every_25_checks(self):
        lines = []
        sh = start_shadow.StartShadow(lines.append, lib=self.FakeLib("mp4"))
        for _ in range(50):
            self.assertTrue(sh.check("mp4", "/a.mp4"))
        self.assertEqual(len([x for x in lines if "decision route=mp4" in x]), 1)
        self.assertEqual(len([x for x in lines if "summary" in x]), 2)
        self.assertEqual((sh.checks, sh.mismatches), (50, 0))

    def test_unavailable_failing_or_throwing_never_raises_and_counts_nothing(self):
        gone = start_shadow.StartShadow(lambda s: None, lib=None)
        gone.available = False
        self.assertIsNone(gone.check("mp4", "/a.mp4"))
        lines = []
        failing = start_shadow.StartShadow(lines.append, lib=self.FakeLib("__fail__"))
        self.assertIsNone(failing.check("mp4", "/a.mp4"))
        self.assertEqual(failing.checks, 0)
        self.assertTrue(any("start call failed" in x for x in lines))

        def boom(_):
            raise RuntimeError("log is broken")

        noisy = start_shadow.StartShadow(boom, lib=self.FakeLib("mp3"))
        self.assertFalse(noisy.check("mp4", "/a.mp4"), "a broken logger does not stop the comparison")

    def test_facts_that_are_none_are_left_out_of_the_request(self):
        lib = self.FakeLib("mp4")
        sh = start_shadow.StartShadow(lambda s: None, lib=lib)
        sh.check("mp4", "/a.mp4", has_primary=True, primary_exists=True)
        self.assertEqual(lib.requests[0], {"external": False, "info": {"str": "/a.mp4"}, "has_primary": True, "primary_exists": True})
        sh.check("mp4", "/a.mp4", pair=False, mp3g=True, zip_cached=False)
        self.assertEqual(lib.requests[1], {"external": False, "info": {"str": "/a.mp4"}, "has_primary": False, "zip_cached": False, "mp3g": True, "pair": False})


if __name__ == "__main__":
    unittest.main()
