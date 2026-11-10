"""karafun_web.py: choosing / downloading the browser (no network: a fake server and a tiny zip), and the helper's command lines."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import karafun_web


def fake_zip(root: Path, name_ok=True) -> bytes:  # name_ok: True = a program that names itself right, "wrong" = one that does not, False = none
    """A zip shaped like Chrome for Testing's (the app's program file inside chrome-<platform>/)."""
    src = root / "src"
    binary = src / f"chrome-{karafun_web._platform_key()}" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
    if name_ok:  # True or "wrong"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho Google Chrome for Testing 999.0.1\n" if name_ok is True else "#!/bin/sh\necho Something Else 1.0\n")
        binary.chmod(0o755)
    else:
        (src / "elsewhere").mkdir(parents=True)
        (src / "elsewhere" / "x").write_text("x")
    out = root / "fake.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", str(src), str(out)], check=True)
    return out.read_bytes()


class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def server(zip_bytes, length=None):
    index = {"channels": {"Stable": {"version": "999.0.1", "downloads": {"chrome": [
        {"platform": "mac-arm64", "url": "https://storage.googleapis.com/x/a.zip"}, {"platform": "mac-x64", "url": "https://storage.googleapis.com/x/b.zip"}]}}}}
    def opener(url, timeout=None):
        return Response(json.dumps(index).encode()) if url.endswith(".json") else Response(zip_bytes, length)
    return opener


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"SINGWS_HOME": str(self.root / "home")})
        patcher.start(); self.addCleanup(patcher.stop); self.addCleanup(self.tmp.cleanup)

    def test_an_installed_chrome_is_used_and_nothing_is_downloaded(self):
        with mock.patch.object(karafun_web, "find_chrome", return_value=Path("/x/chrome")):
            self.assertEqual(karafun_web.ensure_browser(urlopen=mock.Mock(side_effect=AssertionError("downloaded"))), Path("/x/chrome"))

    def test_it_downloads_once_unpacks_checks_and_reuses(self):
        data = fake_zip(self.root)
        seen = []
        with mock.patch.object(karafun_web, "find_chrome", return_value=None):
            first = karafun_web.ensure_browser(lambda d, t: seen.append((d, t)), urlopen=server(data), verify=True)
            self.assertTrue(first.is_file())
            self.assertIn("999.0.1", str(first))
            self.assertEqual(seen[-1][0], len(data))
            # the second time nothing is fetched
            again = karafun_web.ensure_browser(urlopen=mock.Mock(side_effect=AssertionError("downloaded twice")), verify=True)
            self.assertEqual(again, first)
        leftovers = [p.name for p in karafun_web.browser_root().iterdir() if p.name.startswith(".download")]
        self.assertEqual(leftovers, [])

    def test_a_short_download_installs_nothing(self):
        data = fake_zip(self.root)
        with mock.patch.object(karafun_web, "find_chrome", return_value=None):
            with self.assertRaises(RuntimeError):
                karafun_web.ensure_browser(urlopen=server(data, length=len(data) + 10), verify=False)
        self.assertIsNone(karafun_web.downloaded_chrome())
        self.assertEqual([p.name for p in karafun_web.browser_root().iterdir()], [])

    def test_a_zip_without_the_program_installs_nothing(self):
        data = fake_zip(self.root, name_ok=False)
        with mock.patch.object(karafun_web, "find_chrome", return_value=None):
            with self.assertRaises(RuntimeError):
                karafun_web.ensure_browser(urlopen=server(data), verify=False)
        self.assertIsNone(karafun_web.downloaded_chrome())

    def test_a_program_that_does_not_name_itself_right_installs_nothing(self):
        data = fake_zip(self.root, name_ok="wrong")
        with mock.patch.object(karafun_web, "find_chrome", return_value=None):
            with self.assertRaises(RuntimeError):
                karafun_web.ensure_browser(urlopen=server(data), verify=True)
        self.assertIsNone(karafun_web.downloaded_chrome())

    def test_only_googles_download_host_is_accepted(self):
        index = {"channels": {"Stable": {"version": "1", "downloads": {"chrome": [
            {"platform": karafun_web._platform_key(), "url": "https://example.invalid/a.zip"}]}}}}
        opener = lambda url, timeout=None: Response(json.dumps(index).encode())
        with mock.patch.object(karafun_web, "find_chrome", return_value=None):
            with self.assertRaises(RuntimeError):
                karafun_web.ensure_browser(urlopen=opener, verify=False)

    def test_only_https_downloads_are_accepted(self):
        index = {"channels": {"Stable": {"version": "1", "downloads": {"chrome": [
            {"platform": karafun_web._platform_key(), "url": "http://storage.googleapis.com/a.zip"}]}}}}
        opener = lambda url, timeout=None: Response(json.dumps(index).encode())
        with mock.patch.object(karafun_web, "find_chrome", return_value=None):
            with self.assertRaises(RuntimeError):
                karafun_web.ensure_browser(urlopen=opener, verify=False)


class CommandTests(unittest.TestCase):
    def test_commands_are_json_lines_with_safe_quoting(self):
        written = []
        proc = mock.Mock()
        proc.stdin.write.side_effect = written.append
        k = karafun_web.KaraFunWeb(helper_path="/x/helper")
        k._proc = proc
        k.play(3241559, 'NOFX "Linoleum"\n')
        k.set_key(-2); k.set_tempo(90); k.seek(12.5); k.set_volume(55)
        lines = [json.loads(w.decode()) for w in written]
        self.assertEqual(lines[0], {"cmd": "play", "id": 3241559, "query": 'NOFX "Linoleum"\n'})
        self.assertEqual(lines[1:], [{"cmd": "key", "semitones": -2}, {"cmd": "tempo", "percent": 90}, {"cmd": "seek", "seconds": 12.5}, {"cmd": "volume", "volume": 55.0}])


if __name__ == "__main__":
    unittest.main()


class ServiceTests(unittest.TestCase):
    class FakeWeb:
        def __init__(self, **kw):
            self.kw = kw; self.started = False; self.signed_in_called = 0; self.closed = False
        def start(self): self.started = True
        def sign_in(self): self.signed_in_called += 1
        def command(self, name, **kw): self.commands = getattr(self, 'commands', []) + [name]
        def login(self, email, password): self.logins = getattr(self, 'logins', []) + [(email, password)]
        def close(self): self.closed = True

    def make(self, find=None, credentials=None):
        states = []
        holder = {}
        def make_web(**kw):
            holder["web"] = self.FakeWeb(**kw)
            return holder["web"]
        service = karafun_web.KaraFunWebService(lambda s, d: states.append((s, d)), profile_dir="/tmp/x", make_web=make_web,
                                                find_browser=find or (lambda progress: Path("/x/chrome")), credentials=credentials)
        return service, states, holder

    def go(self, service):
        service.start(); service._thread.join(5)

    def test_the_browser_is_found_the_player_started_and_state_reported(self):
        service, states, holder = self.make()
        self.go(service)
        self.assertEqual([s for s, _ in states], ["preparing", "starting"])
        self.assertTrue(holder["web"].started)
        self.assertEqual(holder["web"].kw["chrome_path"], Path("/x/chrome"))

    def test_download_progress_is_shown_as_a_percentage(self):
        def find(progress):
            progress(50, 200); progress(200, 200)
            return Path("/x/chrome")
        service, states, _ = self.make(find)
        self.go(service)
        self.assertIn(("preparing", "Downloading the browser (25%)"), states)
        self.assertIn(("preparing", "Downloading the browser (100%)"), states)

    def test_a_failure_is_an_error_state_with_the_reason_and_can_be_retried(self):
        calls = []
        def find(progress):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("no internet")
            return Path("/x/chrome")
        service, states, holder = self.make(find)
        self.go(service)
        self.assertEqual(states[-1], ("error", "no internet"))
        self.go(service)
        self.assertTrue(holder["web"].started)

    def signed_out(self, service, n):
        for _ in range(n):
            service._message({"type": "status", "signedIn": False})

    def test_signed_in_is_ready(self):
        service, states, holder = self.make()
        self.go(service)
        service._message({"type": "status", "signedIn": True, "plan": "Premium"})
        self.assertEqual(states[-1], ("ready", "Signed in (Premium)"))

    def test_nothing_is_opened_while_the_profile_signs_itself_in(self):
        service, states, holder = self.make()
        self.go(service)
        self.signed_out(service, 3)
        self.assertEqual(states[-1][0], "starting")              # a page still starting is not "signed out"
        self.signed_out(service, 1)
        self.assertEqual(states[-1][0], "sign_in")
        self.signed_out(service, 50)
        self.assertEqual(holder["web"].signed_in_called, 0)       # the browser page does not open in the first 30 s

    def test_without_a_saved_login_the_browser_page_opens_once_after_30_seconds(self):
        service, states, holder = self.make()
        self.go(service)
        self.signed_out(service, 60)
        self.assertEqual(holder["web"].signed_in_called, 1)
        self.signed_out(service, 100)
        self.assertEqual(holder["web"].signed_in_called, 1)

    def test_a_saved_login_is_used_first_then_the_browser_page_if_it_does_not_work(self):
        service, states, holder = self.make(credentials=lambda: ("a@b.c", "pw"))
        self.go(service)
        self.signed_out(service, 60)
        self.assertEqual(holder["web"].logins, [("a@b.c", "pw")])
        self.assertEqual(holder["web"].signed_in_called, 0)
        self.signed_out(service, 59)
        self.assertEqual(holder["web"].signed_in_called, 0)
        self.signed_out(service, 1)
        self.assertEqual(holder["web"].signed_in_called, 1)
        self.signed_out(service, 100)
        self.assertEqual(len(holder["web"].logins), 1)            # tried once only: no hammering KaraFun's login

    def test_a_saved_login_that_works_reloads_the_player_once_then_is_ready(self):
        service, states, holder = self.make(credentials=lambda: ("a@b.c", "pw"))
        self.go(service)
        self.signed_out(service, 60)
        signed = {"type": "status", "signedIn": True, "plan": "Pro"}
        service._message(signed)
        self.assertEqual(holder["web"].commands, ["reload"])
        self.assertNotEqual(states[-1][0], "ready")              # not ready until the reloaded page is signed in
        with mock.patch.object(karafun_web.time, "monotonic", return_value=karafun_web.time.monotonic() + 1):
            service._message(signed)                              # a stale status from the page being reloaded
        self.assertNotEqual(states[-1][0], "ready")
        with mock.patch.object(karafun_web.time, "monotonic", return_value=karafun_web.time.monotonic() + 20):
            service._message(signed)
        self.assertEqual(states[-1][0], "ready")
        service._message(signed)
        self.assertEqual(holder["web"].commands, ["reload"])      # only once
        self.assertEqual(holder["web"].signed_in_called, 0)       # and the browser page never opened

    def test_an_unreadable_keychain_falls_back_to_the_browser_page(self):
        def broken():
            raise OSError("keychain locked")
        service, states, holder = self.make(credentials=broken)
        self.go(service)
        self.signed_out(service, 60)
        self.assertEqual(holder["web"].signed_in_called, 1)

    def test_the_players_messages_still_reach_the_host(self):
        got = []
        service = karafun_web.KaraFunWebService(lambda s, d: None, on_message=got.append, profile_dir="/tmp/x", make_web=lambda **kw: self.FakeWeb(**kw),
                                                find_browser=lambda p: Path("/x/chrome"))
        service._message({"type": "log", "message": "hello"})
        self.assertEqual(got, [{"type": "log", "message": "hello"}])
