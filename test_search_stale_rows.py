"""2026-11-19 show: searching "vandals" showed rows for "va" (Viva Mexico, Vas a quedarte, Tu t'en vas ...). `_apply_db_search_results` fills the list in batches of 10 from a
QTimer; the next search cleared the list but the OLD chain of batches kept running and added its rows under the new query. A chain now stops when a newer search
or a newer delivery has taken over."""
import _test_profile  # noqa: F401
import unittest
from unittest import mock

from test_recent_regressions import load_main_module, make_app


class FakeList:
    def __init__(self):
        self.items = []

    def clear(self):
        self.items = []

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def currentRow(self):
        return 0

    def setCurrentRow(self, row):
        pass

    def setUpdatesEnabled(self, on):
        pass


class FakeInput:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class StaleSearchRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make(self, query):
        app = make_app(self.singws)
        app.results_list = FakeList()
        app.search_input = FakeInput(query)
        app.track_map = {}
        app._search_job_id = 1
        app._search_render_gen = 0
        app._row_disc_role = 0
        app._track_from_search_row = lambda r: dict(r)
        app._build_search_row_text = lambda t: (t["title"], t["title"], t["title"], "")
        app._set_aligned_row_meta = lambda item, left, right: None
        app._start_pending_search_if_needed = lambda job: None
        app.library_filter_bar = None
        self.timers = []
        patcher = mock.patch.object(self.singws.QTimer, "singleShot", staticmethod(lambda ms, fn: self.timers.append(fn)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: None)
        # QListWidgetItem needs no application for plain text
        return app

    def rows(self, prefix, n):
        return [{"artist": "A", "title": f"{prefix} {i}"} for i in range(n)]

    def run_chain(self):
        while self.timers:
            self.timers.pop(0)()

    def titles(self, app):
        return [it.data(self.singws.Qt.ItemDataRole.UserRole)["title"] for it in app.results_list.items]

    def test_a_newer_search_stops_the_old_chain_from_adding_its_rows(self):
        app = self.make("va")
        app._apply_db_search_results(1, self.rows("va", 35))
        self.assertEqual(len(self.titles(app)), 10, "the first batch of the 'va' rows is in")
        # the operator types on: a new job, the list is cleared (as search_tracks does)
        app._search_job_id = 2
        app._search_render_gen += 1
        app.results_list.clear()
        self.run_chain()
        self.assertEqual(self.titles(app), [], "the old rows never arrive")

    def test_a_second_delivery_of_the_same_search_replaces_the_first_without_mixing(self):
        app = self.make("vandals")
        app._apply_db_search_results(1, self.rows("strict", 35))       # strict + KaraFun rows
        app._apply_db_search_results(1, self.rows("fuzzy", 25))        # the fuzzy delivery of the same job
        self.run_chain()
        titles = self.titles(app)
        self.assertTrue(titles and all(t.startswith("fuzzy") for t in titles), titles)
        self.assertEqual(len(titles), 25)

    def test_a_search_that_is_still_current_fills_the_whole_list(self):
        app = self.make("vandals")
        app._apply_db_search_results(1, self.rows("row", 35))
        self.run_chain()
        self.assertEqual(len(self.titles(app)), 35)


if __name__ == "__main__":
    unittest.main()
