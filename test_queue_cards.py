"""Show tab redesign queue cards: a singer with several songs is ONE card (chip centred, no seam, selection lights the whole card)."""
import importlib.util
import os
import tempfile
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _close_all_widgets():
    """These tests show real widgets; leave nothing behind (a leftover focus widget made unrelated tests think the host was busy elsewhere)."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        if type(w).__name__ == "KaraokeApp":
            continue                      # a whole app window owned by another test: closing it runs the full shutdown and can block
        try:
            w.hide()
            w.close()
            w.deleteLater()
        except Exception:
            pass
    app.processEvents()


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_qcards", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class QueueCardTests(unittest.TestCase):
    def tearDown(self):
        _close_all_widgets()

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def build(self, counts):
        from PyQt6.QtCore import Qt
        m = self.m
        L, R, K, P = [int(Qt.ItemDataRole.UserRole) + n for n in (20, 21, 30, 33)]
        rows = []
        for i, n in enumerate(counts):
            rows.append({"kind": "singer", "roles": {L: f"{i + 1}. Singer {i}", R: "", K: "singer", P: ""}, "height": 34})
            for j in range(n):
                rows.append({"kind": "song", "roles": {L: f"• Artist {i} • Song {j}", R: "03:20", K: "song", P: ""}, "height": 31})
        view = m.QueueListView()
        model = m.QueueListModel(view)
        model.setRows(rows)
        view.setModel(model)
        d = m.QueueCardDelegate(L, R, K, P, parent=view)
        view.setItemDelegate(d)
        d.attach(view)
        view.setSpacing(0)
        view.setStyleSheet("QListView { background: #0a1422; border: none; }")   # a dark ground, so the translucent cards show
        view.resize(520, 700)
        view.show()
        self.app.processEvents()
        return view, model, d

    def test_card_edges_for_one_two_and_four_songs(self):
        view, model, d = self.build([1, 2, 4])
        edges = [d.card_edges(model.index(r, 0)) for r in range(model.rowCount())]
        # singer rows are always first; the last song of each singer closes the card; a singer's middle songs do neither
        self.assertEqual([e for e in edges if e[0]], [(True, False)] * 3)
        self.assertEqual(sum(1 for e in edges if e[1]), 3)
        self.assertEqual(edges[-1], (False, True))

    def test_the_chip_is_centred_on_the_whole_card(self):
        view, model, d = self.build([1, 2, 4])
        self.assertEqual(d._card_mid(model.index(0, 0)), (d.NAME_H + 1 * d.SONG_H) // 2)
        self.assertEqual(d._card_mid(model.index(2, 0)), (d.NAME_H + 2 * d.SONG_H) // 2)
        self.assertEqual(d._card_mid(model.index(5, 0)), (d.NAME_H + 4 * d.SONG_H) // 2)

    def test_it_paints_every_row_without_a_seam_inside_a_card(self):
        view, model, d = self.build([3])
        img = view.viewport().grab().toImage()
        x = 200                                    # clear of the chip, the text and the right-hand time
        top = view.visualRect(model.index(0, 0))
        boundary = view.visualRect(model.index(1, 0)).top()
        rows = [img.pixelColor(x, y).name() for y in range(boundary - 3, boundary + 3)]
        self.assertEqual(len(set(rows)), 1, f"a line shows between the name row and the first song: {rows}")
        self.assertGreater(top.height(), 0)

    def test_a_singer_with_no_song_has_a_dimmer_name(self):
        view, model, d = self.build([0, 2])
        img = view.viewport().grab().toImage()

        def brightest(row):
            r = view.visualRect(model.index(row, 0))
            return max(img.pixelColor(x, y).red() for x in range(r.left() + 44, r.left() + 120) for y in range(r.top() + 4, r.bottom() - 2))
        empty_name, full_name = brightest(0), brightest(1)
        self.assertLess(empty_name, full_name - 40, f"empty singer {empty_name} vs singer with songs {full_name}")

    def test_selecting_a_song_selects_the_look_of_the_whole_card(self):
        view, model, d = self.build([3, 2])
        plain = view.viewport().grab().toImage().pixelColor(200, view.visualRect(model.index(0, 0)).top() + 6).name()
        view.setCurrentIndex(model.index(2, 0))      # the second song of the first singer
        self.app.processEvents()
        for r in (0, 1, 2, 3):
            y = view.visualRect(model.index(r, 0)).top() + 6
            self.assertNotEqual(view.viewport().grab().toImage().pixelColor(200, y).name(), plain, f"row {r} did not light up")
        other = view.viewport().grab().toImage().pixelColor(200, view.visualRect(model.index(4, 0)).top() + 12).name()
        self.assertEqual(other, plain)               # the next singer's card is untouched

    def test_moving_the_selection_to_another_card_clears_the_old_card_completely(self):
        view, model, d = self.build([3, 2])
        plain = view.viewport().grab().toImage().pixelColor(200, view.visualRect(model.index(4, 0)).top() + 12).name()
        view.setCurrentIndex(model.index(2, 0))          # a song of the first card
        self.app.processEvents()
        lit = view.viewport().grab().toImage().pixelColor(200, view.visualRect(model.index(1, 0)).top() + 6).name()
        view.setCurrentIndex(model.index(5, 0))          # a song of the second card
        self.app.processEvents()
        img = view.viewport().grab().toImage()
        for r in (0, 1, 2, 3):                           # every row of the first card is back to normal...
            self.assertNotEqual(img.pixelColor(200, view.visualRect(model.index(r, 0)).top() + 6).name(), lit, f"row {r} stayed lit")
        for r in (4, 5, 6):                              # ...and the second card is lit as one block
            self.assertNotEqual(img.pixelColor(200, view.visualRect(model.index(r, 0)).top() + 12).name(), plain)

    def test_a_selection_change_repaints_the_whole_viewport_not_just_two_rows(self):
        # This is the "stays highlighted" bug: Qt repaints only the old and new rows, the other rows of the old card keep their tint.
        from PyQt6.QtCore import QEvent, QObject
        view, model, d = self.build([3, 3])
        painted = []

        class Spy(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Paint:
                    painted.append(ev.rect())
                return False

        spy = Spy(view)
        view.viewport().installEventFilter(spy)
        view.setCurrentIndex(model.index(1, 0))
        self.app.processEvents()
        painted.clear()
        view.setCurrentIndex(model.index(6, 0))
        self.app.processEvents()
        covered = view.viewport().rect()
        self.assertTrue(any(r.contains(covered) for r in painted), f"only {[tuple(r.getRect()) for r in painted]} was repainted")

    def test_the_exact_selected_row_stands_out_from_the_rest_of_its_lit_card(self):
        view, model, d = self.build([3])
        view.setCurrentIndex(model.index(2, 0))          # the second song
        self.app.processEvents()
        img = view.viewport().grab().toImage()
        x = 200
        def px(row):
            return img.pixelColor(x, view.visualRect(model.index(row, 0)).top() + 4).name()
        sibling = px(1)
        self.assertEqual(px(0), sibling)                 # name row and the other songs share the soft card tint
        self.assertEqual(px(3), sibling)
        self.assertNotEqual(px(2), sibling)              # the picked song has its own band

    def test_the_band_is_centred_on_the_text_of_the_selected_row(self):
        view, model, d = self.build([3])
        view.setCurrentIndex(model.index(2, 0))
        self.app.processEvents()
        img = view.viewport().grab().toImage()
        rect = view.visualRect(model.index(2, 0))
        x_band, x_text = rect.left() + 80, None
        base = img.pixelColor(x_band + 160, view.visualRect(model.index(1, 0)).top() + 2)      # a lit-card row that is not the band
        band_rows = [y for y in range(rect.top(), rect.bottom() + 1) if img.pixelColor(x_band + 160, y).blue() - base.blue() > 25]
        self.assertTrue(band_rows, "no band found")
        band_mid = (band_rows[0] + band_rows[-1]) / 2.0
        text_rows = []
        for y in range(rect.top(), rect.bottom() + 1):
            for x in range(rect.left() + 60, rect.left() + 200):
                c = img.pixelColor(x, y)
                if c.red() + c.green() + c.blue() > 480:           # the text pixels (songs are the muted grey)
                    text_rows.append(y)
                    break
        self.assertTrue(text_rows, "no text found")
        text_mid = (text_rows[0] + text_rows[-1]) / 2.0
        self.assertLessEqual(abs(band_mid - text_mid), 3.0, f"band centre {band_mid} vs text centre {text_mid}")

    def test_the_other_rows_of_a_selected_card_are_dimmed(self):
        view, model, d = self.build([3])
        def text_brightness(row):
            img = view.viewport().grab().toImage()
            rect = view.visualRect(model.index(row, 0))
            best = 0
            for xx in range(rect.left() + 60, rect.left() + 200):
                for yy in range(rect.top() + 4, rect.bottom() - 2):
                    c = img.pixelColor(xx, yy)
                    best = max(best, c.red() + c.green() + c.blue())
            return best
        view.setCurrentIndex(model.index(2, 0))
        self.app.processEvents()
        self.assertGreater(text_brightness(2), text_brightness(3))     # picked song is brighter than its sibling

    def test_only_one_card_is_lit_at_a_time_after_many_clicks(self):
        view, model, d = self.build([2, 2, 2])
        for r in (1, 4, 2, 7, 5, 0):
            view.setCurrentIndex(model.index(r, 0))
            self.app.processEvents()
        img = view.viewport().grab().toImage()
        lit_cards = 0
        base = img.pixelColor(200, view.visualRect(model.index(0, 0)).top() + 6).name()   # row 0 is selected last: this is the "lit" colour
        for singer_row in (0, 3, 6):
            if img.pixelColor(200, view.visualRect(model.index(singer_row, 0)).top() + 6).name() == base:
                lit_cards += 1
        self.assertEqual(lit_cards, 1)

    def _amber_pixels(self, view, row):
        img = view.viewport().grab().toImage()
        rect = view.visualRect(view.model().index(row, 0))
        count = 0
        for x in range(rect.left(), rect.right()):
            for y in range(rect.top(), rect.bottom(), 2):
                c = img.pixelColor(x, y)
                if c.red() > 200 and 160 < c.green() < 235 and c.blue() < 150:
                    count += 1
        return count

    def test_a_song_of_five_minutes_or_more_gets_a_long_badge_and_a_shorter_one_does_not(self):
        from PyQt6.QtCore import Qt
        view, model, d = self.build([2])
        R = int(Qt.ItemDataRole.UserRole) + 21
        model._rows[1]["roles"][R] = "04:59"
        model._rows[2]["roles"][R] = "05:00"
        model.layoutChanged.emit()
        self.app.processEvents()
        self.assertEqual(self._amber_pixels(view, 1), 0)
        self.assertGreater(self._amber_pixels(view, 2), 20)

    def test_the_badge_never_appears_on_a_singer_row(self):
        from PyQt6.QtCore import Qt
        view, model, d = self.build([1])
        R = int(Qt.ItemDataRole.UserRole) + 21
        model._rows[0]["roles"][R] = "09:00"
        model.layoutChanged.emit()
        self.app.processEvents()
        self.assertEqual(self._amber_pixels(view, 0), 0)

    def test_the_current_singers_green_tint_covers_the_whole_card_not_just_the_name_row(self):
        from PyQt6.QtCore import Qt
        L, R, K, P = [int(Qt.ItemDataRole.UserRole) + n for n in (20, 21, 30, 33)]
        rows = []
        for i, st in enumerate(("current", "")):
            rows.append({"kind": "singer", "roles": {L: f"{i + 1}. S{i}", R: "", K: "singer", P: st}, "height": 34})
            for j in range(2):
                rows.append({"kind": "song", "roles": {L: f"- A - T{j}", R: "03:20", K: "song", P: ""}, "height": 31})
        view = self.m.QueueListView()
        model = self.m.QueueListModel(view)
        model.setRows(rows)
        view.setModel(model)
        d = self.m.QueueCardDelegate(L, R, K, P, parent=view)
        view.setItemDelegate(d)
        view.setSpacing(0)
        view.setStyleSheet("QListView { background: #0a0a14; border: none; }")
        view.resize(520, 400)
        view.show()
        self.app.processEvents()
        img = view.grab().toImage()
        def green_excess(row):
            r = view.visualRect(model.index(row, 0))
            c = img.pixelColor(r.center().x() + 60, r.center().y())          # the middle of the row, away from text
            return c.green() - c.red()
        self.assertGreater(green_excess(2), green_excess(5) + 6)               # the current card's LAST song row is tinted like its name row
        self.assertGreater(green_excess(0), green_excess(3) + 6)

    def test_a_long_queue_paints_and_scrolls(self):
        view, model, d = self.build([2] * 60)
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertFalse(view.viewport().grab().toImage().isNull())


if __name__ == "__main__":
    unittest.main()
