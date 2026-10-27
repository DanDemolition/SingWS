"""Differential parity of the Stage E text view models (ticker, waitlist rows, hero card) against the app's real Python code.

Small fixed-seed runs of tools/ticker_parity.py, tools/waitlist_parity.py and tools/hero_parity.py; each needs its Rust CLI built
(`cd rust && cargo build --release -p singws-uimodel`, with CARGO_TARGET_DIR if it is not the default) and is skipped otherwise."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import unittest
from pathlib import Path

from tools import diagnostics_parity as diagnostics
from tools import header_parity as header
from tools import rows_parity as rows
from tools import hero_parity as hero
from tools import ticker_parity as ticker
from tools import waitlist_parity as waitlist


def _run(mod, cases, seed):
    mod.failures.clear()
    mod.counts.clear()
    return mod.main(["--cases", str(cases), "--seed", str(seed)])


@unittest.skipUnless(Path(ticker.CLI).exists(), "needs singws-ticker-cli")
class TickerTextParity(unittest.TestCase):
    def test_rust_ticker_text_equals_the_apps(self):
        self.assertEqual(_run(ticker, 300, 5), 0, ticker.failures[:3])
        self.assertGreater(ticker.counts.get("with_message", 0), 0)


@unittest.skipUnless(Path(waitlist.CLI).exists(), "needs singws-waitlist-cli")
class WaitlistRowParity(unittest.TestCase):
    def test_rust_waitlist_rows_equal_the_apps(self):
        self.assertEqual(_run(waitlist, 40, 5), 0, waitlist.failures[:3])
        self.assertGreater(waitlist.counts.get("sections_same", 0), 0)
        self.assertGreater(waitlist.counts.get("iso_same", 0), 0)


@unittest.skipUnless(Path(hero.CLI).exists(), "needs singws-hero-cli")
class HeroTextParity(unittest.TestCase):
    def test_rust_hero_text_equals_the_apps(self):
        self.assertEqual(_run(hero, 200, 5), 0, hero.failures[:3])
        self.assertGreater(hero.counts.get("text_same", 0), 0)


@unittest.skipUnless(Path(header.CLI).exists(), "needs singws-header-cli")
class HeaderTextParity(unittest.TestCase):
    def test_rust_header_and_queue_summary_equal_the_apps(self):
        self.assertEqual(_run(header, 120, 5), 0, header.failures[:3])
        self.assertGreater(header.counts.get("eta_same", 0), 0)


@unittest.skipUnless(Path(diagnostics.CLI).exists(), "needs singws-diagnostics-cli")
class DiagnosticsParity(unittest.TestCase):
    def test_rust_pre_show_report_equals_the_apps(self):
        self.assertEqual(_run(diagnostics, 150, 5), 0, diagnostics.failures[:3])
        self.assertGreater(diagnostics.counts.get("same", 0), 0)


@unittest.skipUnless(Path(rows.CLI).exists(), "needs singws-rows-cli")
class RowTextParity(unittest.TestCase):
    def test_rust_song_row_text_equals_the_apps(self):
        self.assertEqual(_run(rows, 150, 5), 0, rows.failures[:3])
        self.assertGreater(rows.counts.get("search_same", 0), 0)
        self.assertGreater(rows.counts.get("queue_song_same", 0), 0)


if __name__ == "__main__":
    unittest.main()
