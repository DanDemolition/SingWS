"""The KaraFun builder shadow wraps the app's static script builders and must never change what they return; it checks each distinct call against the Rust helper."""
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import karafun_builder_shadow as bs  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


def reset():
    bs._seen.clear()
    bs._reported.clear()
    bs._clean_noted = False


class Fake:
    @staticmethod
    def _karafun_seek_script(count, item):
        return ["wrong", str(count), item]

    @staticmethod
    def _karafun_press_play_script():
        return ["wrong"]

    @staticmethod
    def _karafun_click_point_script(point):
        return ["wrong"]

    def _karafun_not_wrapped(self):
        return 1


class Wrapping(unittest.TestCase):
    def setUp(self):
        reset()
        self.logs = []

    def test_nothing_is_wrapped_when_off_or_without_a_helper(self):
        self.assertEqual(bs.install(Fake, {"karafun_builder_shadow": False}, self.logs.append), 0)
        old = bs.base.find_helper
        bs.base.find_helper = lambda: None
        try:
            self.assertEqual(bs.install(Fake, {}, self.logs.append), 0)
        finally:
            bs.base.find_helper = old

    def test_requests_carry_only_plain_values(self):
        self.assertEqual(bs._request("seek", ("count", "item"), {"count": 3, "item": "Skip Forward 10s"}), {"op": "script", "name": "seek", "count": 3, "item": "Skip Forward 10s"})
        self.assertIsNone(bs._request("seek", ("count", "item"), {"count": 3.5, "item": "x"}))
        self.assertIsNone(bs._request("seek", ("count", "item"), {"count": True, "item": "x"}))
        self.assertEqual(bs._request("click_point", ("point",), {"point": (4, 5)}), {"op": "script", "name": "click_point", "x": 4, "y": 5})
        self.assertIsNone(bs._request("click_point", ("point",), {"point": (4.0, 5)}))


class AgainstTheApp(unittest.TestCase):
    def test_every_wrapped_builder_is_a_static_method_of_the_app_with_the_argument_names_the_requests_use(self):
        import ast
        tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
        found = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "KaraokeApp":
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) and sub.name in bs.BUILDERS:
                        static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in sub.decorator_list)
                        found[sub.name] = (static, [a.arg for a in sub.args.args])
        self.assertEqual(sorted(found), sorted(bs.BUILDERS), "a wrapped builder moved or was renamed")
        for name, (static, args) in found.items():
            self.assertTrue(static, name)
            fields = bs.BUILDERS[name][1] or ()
            self.assertEqual(args, list(fields), f"{name}: the request field names must be the builder's argument names")


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithHelper(unittest.TestCase):
    def setUp(self):
        reset()
        self.logs = []
        os.environ["SINGWS_KARAFUN_BIN"] = str(BIN)

    def tearDown(self):
        os.environ.pop("SINGWS_KARAFUN_BIN", None)

    def wait_logs(self, n=1):
        end = time.time() + 15
        while time.time() < end and len(self.logs) < n:
            time.sleep(0.05)

    def test_the_wrapper_returns_exactly_what_the_builder_returned_and_logs_a_difference(self):
        class K(Fake):
            pass
        self.assertGreaterEqual(bs.install(K, {}, self.logs.append), 3)
        self.assertEqual(K._karafun_seek_script(2, "Skip Forward 10s"), ["wrong", "2", "Skip Forward 10s"])
        self.assertEqual(K()._karafun_not_wrapped(), 1)
        self.wait_logs()
        self.assertTrue(any("MISMATCH seek" in l for l in self.logs), self.logs)
        self.assertFalse(any("Skip Forward" in l or "wrong" in l for l in self.logs), "the log must not carry script text or arguments")

    def test_a_script_equal_to_the_helpers_is_clean_and_a_repeat_is_not_rechecked(self):
        req = {"op": "script", "name": "seek", "count": 2, "item": "Skip Forward 10s"}
        lines = bs._run(BIN, req)["lines"]

        class K:
            calls = 0

            @staticmethod
            def _karafun_seek_script(count, item):
                K.calls += 1
                return list(lines)
        bs.install(K, {}, self.logs.append)
        for _ in range(5):
            K._karafun_seek_script(2, "Skip Forward 10s")
        self.wait_logs()
        time.sleep(0.5)
        self.assertEqual([l for l in self.logs if "MISMATCH" in l], [])
        self.assertEqual(len(bs._seen["seek"]), 1)


if __name__ == "__main__":
    unittest.main()
