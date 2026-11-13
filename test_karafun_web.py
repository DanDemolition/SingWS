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


def chromium_zip(root, version_text="Chromium 160.0.1.2", with_app=True):
    src = root / "zipsrc"
    if with_app:
        binary = src / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!/bin/sh\necho '{version_text}'\n")
        binary.chmod(0o755)
    else:
        (src / "elsewhere").mkdir(parents=True)
        (src / "elsewhere" / "x").write_text("x")
    out = root / "fake.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", str(src), str(out)], check=True)
    return out.read_bytes()


def chromium_server(zip_bytes, revision="1744193", length=None):
    def opener(url, timeout=None):
        return Response(revision.encode()) if url.endswith("LAST_CHANGE") else Response(zip_bytes, length)
    return opener


class BrowserTests(unittest.TestCase):
    """Chromium is downloaded only after the user agreed; Chrome and Chrome for Testing are never used."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"SINGWS_HOME": str(self.root / "home"), "HOME": str(self.root / "nohome")})
        patcher.start(); self.addCleanup(patcher.stop); self.addCleanup(self.tmp.cleanup)
        # an installed /Applications/Chromium.app on this Mac must not change the results
        no_app = mock.patch.object(karafun_web, "_installed_bases", return_value=[])
        no_app.start(); self.addCleanup(no_app.stop)

    def test_without_chromium_nothing_downloads_until_the_user_agrees(self):
        opener = mock.Mock(side_effect=AssertionError("must not download"))
        with self.assertRaises(karafun_web.BrowserMissing):
            karafun_web.ensure_browser(urlopen=opener)

    def test_it_downloads_once_after_consent_checks_it_runs_and_reuses_it(self):
        data = chromium_zip(self.root)
        seen = []
        got = karafun_web.ensure_browser(lambda d, t: seen.append((d, t)), urlopen=chromium_server(data), allow_download=True)
        self.assertTrue(got.is_file())
        self.assertEqual(got.parent.parent.parent.parent.name, "r1744193")
        self.assertEqual(seen[-1][0], len(data))
        again = karafun_web.ensure_browser(urlopen=mock.Mock(side_effect=AssertionError("downloaded twice")))
        self.assertEqual(again, got)
        leftovers = [p.name for p in karafun_web.browser_root().iterdir() if p.name.startswith(".download")]
        self.assertEqual(leftovers, [])

    def test_a_revision_that_is_not_a_number_installs_nothing(self):
        with self.assertRaises(RuntimeError):
            karafun_web.ensure_browser(urlopen=chromium_server(chromium_zip(self.root), revision="../evil"), allow_download=True)
        self.assertFalse((karafun_web.browser_root().parent / "evil").exists())

    def test_a_short_download_installs_nothing(self):
        data = chromium_zip(self.root)
        with self.assertRaises(RuntimeError):
            karafun_web.ensure_browser(urlopen=chromium_server(data, length=len(data) + 10), allow_download=True)
        self.assertIsNone(karafun_web.find_browser())

    def test_a_zip_without_chromium_installs_nothing(self):
        with self.assertRaises(RuntimeError):
            karafun_web.ensure_browser(urlopen=chromium_server(chromium_zip(self.root, with_app=False)), allow_download=True)
        self.assertIsNone(karafun_web.find_browser())

    def test_a_program_that_is_not_chromium_installs_nothing(self):
        with self.assertRaises(RuntimeError):
            karafun_web.ensure_browser(urlopen=chromium_server(chromium_zip(self.root, version_text="Google Chrome 1")), allow_download=True)
        self.assertIsNone(karafun_web.find_browser())

    def test_only_the_chromium_projects_host_and_https_are_used(self):
        with mock.patch.object(karafun_web, "_snapshot_base", return_value="https://evil.example.com/x"):
            with self.assertRaisesRegex(RuntimeError, "not the Chromium project"):
                karafun_web.ensure_browser(urlopen=chromium_server(b"x"), allow_download=True)
        with mock.patch.object(karafun_web, "_snapshot_base", return_value="http://commondatastorage.googleapis.com/x"):
            with self.assertRaises(RuntimeError):
                karafun_web.ensure_browser(urlopen=chromium_server(b"x"), allow_download=True)

    def test_no_chrome_or_chrome_for_testing_code_is_left(self):
        for gone in ("find_chrome", "downloaded_chrome", "_cft_binary", "bundled_browser"):
            self.assertFalse(hasattr(karafun_web, gone), gone)


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
                                                find_browser=find or (lambda progress, **kw: Path("/x/chrome")), credentials=credentials)
        return service, states, holder

    def go(self, service):
        service.start(); service._thread.join(5)

    def test_the_browser_is_found_the_player_started_and_state_reported(self):
        service, states, holder = self.make()
        self.go(service)
        self.assertEqual([s for s, _ in states], ["preparing", "starting"])
        self.assertTrue(holder["web"].started)
        self.assertEqual(holder["web"].kw["chrome_path"], Path("/x/chrome"))

    def test_a_missing_chromium_asks_instead_of_downloading(self):
        def find(progress, allow_download=False, **kw):
            if not allow_download:
                raise karafun_web.BrowserMissing("needs Chromium")
            progress(50, 200)
            return Path("/x/chrome")
        service, states, holder = self.make(find)
        self.go(service)
        self.assertEqual(states[-1][0], "needs_browser")
        self.assertIsNone(holder.get("web"))
        service.start(download=True)
        service._thread.join(5)
        self.assertIn(("preparing", "Downloading Chromium (25%)"), states)
        self.assertEqual(states[-1][0], "starting")

    def test_a_failure_is_an_error_state_with_the_reason_and_can_be_retried(self):
        calls = []
        def find(progress, **kw):
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

    def test_a_browser_that_dies_is_an_error_state_but_our_own_stop_is_not(self):
        service, states, holder = self.make()
        self.go(service)
        service._message({"type": "status", "signedIn": True, "plan": "Pro"})
        self.assertEqual(states[-1][0], "ready")
        service._message({"type": "error", "message": "Chrome stopped"})
        self.assertEqual(states[-1], ("error", "The player's browser stopped"))
        service2, states2, holder2 = self.make()
        self.go(service2)
        service2._message({"type": "status", "signedIn": True, "plan": "Pro"})
        service2.stop()                                              # clears `web` first, as the real stop does
        n = len(states2)
        service2._message({"type": "error", "message": "Chrome stopped"})
        self.assertEqual(len(states2), n)                            # the helper's goodbye after OUR stop is not an error

    def test_the_players_messages_still_reach_the_host(self):
        got = []
        service = karafun_web.KaraFunWebService(lambda s, d: None, on_message=got.append, profile_dir="/tmp/x", make_web=lambda **kw: self.FakeWeb(**kw),
                                                find_browser=lambda p, **kw: Path("/x/chrome"))
        service._message({"type": "log", "message": "hello"})
        self.assertEqual(got, [{"type": "log", "message": "hello"}])
