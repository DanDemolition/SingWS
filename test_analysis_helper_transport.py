"""Exercise real pipes and the batch boundary without touching live show data."""
import sys
import unittest
from unittest import mock

from libmpv_media_jobs import AnalysisHelperError, AnalysisTrackError, IsolatedLoudnessSession


class HelperPipeTests(unittest.TestCase):
    def test_reported_track_errors_do_not_disable_the_helper(self):
        code = '''import sys
for line in sys.stdin:
 if 'quit' in line: break
 sys.stdout.write('SINGWS_ANALYSIS_RESULT {"ok":false,"error":"no integrated loudness"}\\n')
 sys.stdout.flush()
'''
        with IsolatedLoudnessSession(command=[sys.executable, '-u', '-c', code]) as session:
            for _ in range(5):
                with self.assertRaises(AnalysisTrackError):
                    session.measure('unused', timeout=1)
                self.assertTrue(session.usable)

    def test_startup_log_and_result_in_same_write(self):
        code = '''import sys
for line in sys.stdin:
 if 'quit' in line: break
 sys.stdout.write('startup log\\nSINGWS_ANALYSIS_RESULT {"ok":true,"lufs":-14,"peak":-1}\\n')
 sys.stdout.flush()
'''
        with IsolatedLoudnessSession(command=[sys.executable, '-u', '-c', code]) as session:
            for _ in range(3):
                self.assertEqual(session.measure('unused', timeout=0.1), (-14, -1))

    def test_partial_log_does_not_block_timeout(self):
        code = '''import sys,time
sys.stdin.readline()
sys.stdout.write('partial log');sys.stdout.flush()
time.sleep(30)
'''
        with IsolatedLoudnessSession(command=[sys.executable, '-u', '-c', code]) as session:
            with self.assertRaisesRegex(AnalysisHelperError, 'timed out'):
                session.measure('unused', timeout=0.1)


class BatchHelperFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_recent_regressions import load_main_module
        cls.app = load_main_module()

    def test_bad_track_is_not_cached_and_following_track_is_measured(self):
        session = mock.Mock(isolated=True, usable=True)
        session.measure_karaoke_transition.side_effect = [
            AnalysisTrackError('no integrated loudness'),
            (-18.0, -2.0, 180.0, 0.0, 179.0),
        ]
        worker = self.app.AnalyzeLibraryWorker([
            ('karaoke', '/tmp/bad.mp3', 'Bad'),
            ('karaoke', '/tmp/good.mp3', 'Good'),
        ], finalize_cache=False)
        with mock.patch('libmpv_media_jobs.IsolatedLoudnessSession', return_value=session), \
             mock.patch.object(self.app, '_loudness_workers_allowed', return_value=True), \
             mock.patch.object(self.app, 'loudness_failed_cached', return_value=False), \
             mock.patch.object(self.app, '_loudness_file_sig', return_value=(1,2)), \
             mock.patch.object(self.app, 'loudness_info_cached', return_value=None), \
             mock.patch.object(self.app, '_transition_analysis_cached_sync', return_value=None), \
             mock.patch.object(self.app, '_transition_analysis_store'), \
             mock.patch.dict(self.app._loudness_cache, {}, clear=True), \
             mock.patch.object(self.app, '_loudness_append_checkpoint') as checkpoint, \
             mock.patch.object(self.app, '_loudness_mark_failed') as mark:
            worker.run()
            self.assertNotIn('/tmp/bad.mp3', self.app._loudness_cache)
            self.assertEqual(self.app._loudness_cache['/tmp/good.mp3']['i'], -18.0)
            checkpoint.assert_called_once()
        mark.assert_not_called()
        self.assertFalse(worker.is_cancelled())
        self.assertEqual(session.measure_karaoke_transition.call_count, 2)

    def test_helper_failure_stops_without_poisoning_any_track(self):
        for mode, usable in [('full', True), ('fast', True), ('full', False)]:
            with self.subTest(mode=mode, usable=usable):
                session = mock.Mock(isolated=True, usable=usable)
                session.measure_karaoke_transition.side_effect = AnalysisHelperError('pipe failed')
                session.measure_fast.side_effect = AnalysisHelperError('pipe failed')
                worker = self.app.AnalyzeLibraryWorker(
                    [('karaoke', '/tmp/unused.mp3', 'Song')]*5, mode=mode,
                    parallel=True, finalize_cache=False)
                with mock.patch('libmpv_media_jobs.IsolatedLoudnessSession', return_value=session), \
                     mock.patch.object(self.app, '_loudness_workers_allowed', return_value=True), \
                     mock.patch.object(self.app, 'loudness_failed_cached', return_value=False), \
                     mock.patch.object(self.app, '_loudness_file_sig', return_value=(1,2)), \
                     mock.patch.object(self.app, 'loudness_info_cached', return_value=None), \
                     mock.patch.object(self.app, '_transition_analysis_cached_sync', return_value=None), \
                     mock.patch.object(self.app, '_loudness_mark_failed') as mark:
                    worker.run()
                self.assertTrue(worker.is_cancelled())
                mark.assert_not_called()
                self.assertLessEqual(session.measure_karaoke_transition.call_count, 1)
                self.assertLessEqual(session.measure_fast.call_count, 1)
                session.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
