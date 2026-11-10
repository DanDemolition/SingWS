"""The KaraFun login in the Keychain: round trip with a made-up account, and the host's loader (no real password anywhere)."""
import _test_profile  # noqa: F401
import sys
import unittest
from pathlib import Path
from unittest import mock

import karafun_credentials as creds
from karafun_web_host import KaraFunWebHost

ACCOUNT = "singws-test-account@example.invalid"


@unittest.skipUnless(sys.platform == "darwin", "macOS Keychain")
class KeychainTests(unittest.TestCase):
    """Uses its OWN Keychain service name: the real one holds the operator's saved login (a test run once wiped it)."""
    def setUp(self):
        patcher = mock.patch.object(creds, "SERVICE", "SingWS KaraFun account TEST ONLY")
        patcher.start()
        self.addCleanup(patcher.stop)
        creds.delete(ACCOUNT)

    def tearDown(self):
        creds.delete(ACCOUNT)

    def test_round_trip_keeps_every_kind_of_character(self):
        for password in ("simple1", 'p@ss "w\\ord\' 1', "pé 1", "deadbeef", "a b  c"):
            self.assertTrue(creds.save(ACCOUNT, password))
            self.assertEqual(creds.load(ACCOUNT), password)

    def test_saving_again_replaces_and_delete_removes(self):
        creds.save(ACCOUNT, "one")
        creds.save(ACCOUNT, "two")
        self.assertEqual(creds.load(ACCOUNT), "two")
        creds.delete(ACCOUNT)
        self.assertIsNone(creds.load(ACCOUNT))

    def test_bad_input_is_refused(self):
        self.assertFalse(creds.save("", "x"))
        self.assertFalse(creds.save(ACCOUNT, ""))
        self.assertFalse(creds.save('bad"quote@x.y', "x"))

    def test_the_password_never_appears_in_an_argument_list(self):
        seen = []
        real = creds.subprocess.run
        def spy(cmd, *a, **k):
            seen.append(list(cmd))
            return real(cmd, *a, **k)
        with mock.patch.object(creds.subprocess, "run", spy):
            creds.save(ACCOUNT, "uniquepasswordvalue")
        self.assertFalse(any("uniquepasswordvalue" in part or "uniquepasswordvalue".encode().hex() in part for cmd in seen for part in cmd))


@unittest.skipUnless(sys.platform == "darwin", "macOS Keychain")
class SaveKeepsOtherAccountsTests(unittest.TestCase):
    def test_saving_one_account_does_not_delete_another(self):
        with mock.patch.object(creds, "SERVICE", "SingWS KaraFun account TEST ONLY"):
            other = "singws-test-other@example.invalid"
            try:
                creds.save(other, "other-password")
                creds.save(ACCOUNT, "mine")
                self.assertEqual(creds.load(other), "other-password")
                self.assertEqual(creds.load(ACCOUNT), "mine")
            finally:
                creds.delete(other)
                creds.delete(ACCOUNT)

    def test_the_tests_never_use_the_real_service_name(self):
        self.assertEqual(creds.SERVICE, "SingWS KaraFun account")      # (outside the patch) the real one is unchanged
        src = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn("creds." + "delete()", src)       # a no-argument delete removes EVERY saved login under the service


class HostLoaderTests(unittest.TestCase):
    class App:
        settings = {}

    def host(self, email):
        app = self.App()
        app.settings = {"karafun_web_email": email}
        return KaraFunWebHost(app)

    def test_needs_both_an_email_and_a_saved_password(self):
        with mock.patch.object(creds, "load", return_value="pw") as load:
            self.assertEqual(self.host("a@b.c")._saved_login(), ("a@b.c", "pw"))
            load.assert_called_with("a@b.c")
            self.assertIsNone(self.host("")._saved_login())
        with mock.patch.object(creds, "load", return_value=None):
            self.assertIsNone(self.host("a@b.c")._saved_login())


class SourceGuards(unittest.TestCase):
    def test_the_password_is_never_in_settings_or_the_log(self):
        src = open("0.2.18.1.py", encoding="utf-8").read()
        self.assertNotIn('self.settings["karafun_web_password"', src)
        helper = open("rust/crates/singws-karafun-web/src/main.rs", encoding="utf-8").read()
        self.assertIn('"cmd": value.get("cmd")', helper)           # an unknown/malformed command is reported without its fields
        bridge = open("karafun_bridge.js", encoding="utf-8").read()
        login = bridge[bridge.index("window.singwsLogin"):][:900]
        self.assertNotIn("${email}", login)
        self.assertNotIn("${password}", login)


if __name__ == "__main__":
    unittest.main()
