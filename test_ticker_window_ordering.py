"""Window-ordering safety without starting audio or accessing show data."""
import ast
from functools import lru_cache
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


@lru_cache(maxsize=1)
def ticker_methods():
    tree = ast.parse(Path("0.2.18.1.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "DetachedPainterTicker")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name in {"_order_surface_above_output", "sync_surface_geometry"}]
    return compile(ast.Module(body=methods, type_ignores=[]), "ticker-ordering", "exec")


class TickerWindowOrderingTests(unittest.TestCase):
    def setUp(self):
        self.host = mock.Mock()
        self.host.isVisible.return_value = True
        self.host.isMinimized.return_value = False
        self.view = mock.Mock()
        self.view.winId.return_value = 20
        self.ticker = SimpleNamespace(
            _view=self.view, window=lambda: self.host, isVisible=lambda: True,
            _sync_owner=mock.Mock(), _order_surface_above_output=mock.Mock(),
            mapToGlobal=lambda p: SimpleNamespace(x=lambda: 10, y=lambda: 600),
            width=lambda: 800, height=lambda: 80)
        self.output_ns = mock.Mock()
        self.output_ns.level.return_value = 0
        self.output_ns.windowNumber.return_value = 123
        self.ticker_ns = mock.Mock()
        self.objc = SimpleNamespace(objc_object=lambda c_void_p: SimpleNamespace(
            window=lambda: self.output_ns if c_void_p == 10 else self.ticker_ns))
        self.namespace = {
            "sys": SimpleNamespace(platform="darwin"),
            "QWidget": SimpleNamespace(winId=lambda w: 10),
            "QPoint": lambda x, y: (x, y), "_diag": mock.Mock(),
        }
        exec(ticker_methods(), self.namespace)

    def order(self):
        with mock.patch.dict(sys.modules, {
            "objc": self.objc, "AppKit": SimpleNamespace(NSWindowAbove=1)
        }):
            self.namespace["_order_surface_above_output"](self.ticker, self.host)

    def sync(self):
        self.namespace["sync_surface_geometry"](self.ticker)

    def test_mac_orders_only_ticker_above_audience_not_host_or_desktop(self):
        self.order()
        self.assertEqual(self.ticker_ns.mock_calls, [
            mock.call.setLevel_(0), mock.call.setIgnoresMouseEvents_(True),
            mock.call.orderWindow_relativeTo_(1, 123)])
        self.assertEqual(self.output_ns.mock_calls, [mock.call.level(), mock.call.windowNumber()])
        self.host.raise_.assert_not_called()
        self.view.raise_.assert_not_called()

    def test_follows_audience_level_during_fullscreen_auxiliary_handoff(self):
        self.output_ns.level.return_value = 3
        self.order()
        self.ticker_ns.setLevel_.assert_called_once_with(3)

    def test_missing_native_output_does_not_fall_back_to_floating_raise(self):
        self.output_ns = None
        with self.assertRaises(RuntimeError):
            self.order()
        self.view.raise_.assert_not_called()

    def test_minimized_audience_hides_ticker_without_touching_other_windows(self):
        self.host.isMinimized.return_value = True
        self.sync()
        self.view.hide.assert_called_once()
        self.ticker._order_surface_above_output.assert_not_called()

    def test_hidden_audience_hides_ticker(self):
        self.host.isVisible.return_value = False
        self.sync()
        self.view.hide.assert_called_once()
        self.ticker._order_surface_above_output.assert_not_called()

    def test_restore_shows_and_orders_only_the_ticker(self):
        self.view.isVisible.return_value = False
        self.sync()
        self.view.show.assert_called_once()
        self.view.setGeometry.assert_called_once_with(10, 600, 800, 80)
        self.ticker._order_surface_above_output.assert_called_once_with(self.host)
        self.view.raise_.assert_not_called()

    def test_failed_ordering_hides_strip_instead_of_blocking_host(self):
        self.ticker._order_surface_above_output.side_effect = RuntimeError("unavailable")
        self.sync()
        self.view.hide.assert_called_once()
        self.view.raise_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
