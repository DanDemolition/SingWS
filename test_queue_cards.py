"""Show tab redesign queue cards: a singer with several songs is ONE card (chip centred, no seam, selection lights the whole card)."""
import importlib.util
import os
import tempfile
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_qcards", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class QueueCardTests(unittest.TestCase):
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
        view.setSpacing(0)
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

    def test_a_long_queue_paints_and_scrolls(self):
        view, model, d = self.build([2] * 60)
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertFalse(view.viewport().grab().toImage().isNull())


if __name__ == "__main__":
    unittest.main()
