import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import unittest
from pathlib import Path

from tools import http_parity as hp
from tools import uimodel_parity as up

needs_http = unittest.skipUnless(Path(hp.CLI_DEFAULT).exists() and os.path.exists("/opt/homebrew/bin/openssl"),
                                 "needs singws-http-cli and openssl")
needs_ui = unittest.skipUnless(Path(up.CLI).exists(), "needs singws-uimodel-cli")


@needs_http
class HttpSenderParity(unittest.TestCase):
    def test_requests_and_the_rust_sender_agree_on_a_small_run(self):
        hp.failures.clear()
        hp.counts.clear()
        self.assertEqual(hp.main(["--cases", "20", "--seed", "3"]), 0, hp.failures[:3])
        self.assertGreater(hp.counts.get("random calls", 0), 0)
        self.assertEqual(hp.counts.get("tls trusted"), 1)


@needs_ui
class LibrarySearchParity(unittest.TestCase):
    def test_rust_search_equals_song_index_strict_search(self):
        self.assertEqual(up.main(["--cases", "3", "--seed", "11"]), 0)


if __name__ == "__main__":
    unittest.main()
