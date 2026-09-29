"""Optional hidden KaraFun launch at SingWS startup. No app import."""
import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


class _InlineThread:
    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()


@lru_cache(maxsize=1)
def launcher_source():
    tree = ast.parse(Path('0.2.18.1.py').read_text())
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == '_launch_karafun_at_startup')


def run(settings, pgrep_rc=1, app_path=Path('/Applications/KaraFun.app'), platform='darwin'):
    subprocess = mock.Mock()
    subprocess.run.side_effect = lambda args, **kw: SimpleNamespace(
        returncode=pgrep_rc if args[0].endswith('pgrep') else 0, stdout='', stderr='')
    namespace = {
        'sys': SimpleNamespace(platform=platform),
        'subprocess': subprocess,
        'threading': SimpleNamespace(Thread=_InlineThread),
        '_diag': lambda *a: None,
    }
    exec(compile(ast.Module(body=[launcher_source()], type_ignores=[]), 'startup-launch-test', 'exec'), namespace)
    host = SimpleNamespace(settings=settings, _karafun_application_path=lambda: app_path)
    namespace['_launch_karafun_at_startup'](host)
    return subprocess


class StartupLaunchTests(unittest.TestCase):
    def test_off_by_default_launches_nothing(self):
        self.assertFalse(run({}).run.called)

    def test_enabled_launches_hidden_in_background(self):
        sub = run({'karafun_launch_at_startup': True})
        launch = [c.args[0] for c in sub.run.call_args_list if c.args[0][0].endswith('open')]
        self.assertEqual(launch, [['/usr/bin/open', '-g', '-j', '/Applications/KaraFun.app']])

    def test_skips_when_already_running(self):
        sub = run({'karafun_launch_at_startup': True}, pgrep_rc=0)
        self.assertFalse([c for c in sub.run.call_args_list if c.args[0][0].endswith('open')])

    def test_skips_when_app_missing(self):
        self.assertFalse(run({'karafun_launch_at_startup': True}, app_path=None).run.called)

    def test_not_macos_launches_nothing(self):
        self.assertFalse(run({'karafun_launch_at_startup': True}, platform='win32').run.called)


if __name__ == '__main__':
    unittest.main()
