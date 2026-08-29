import json
from pathlib import Path
import tempfile
import unittest

import transition_analysis as ta


class AudioBoundaryTests(unittest.TestCase):
    def test_detects_confirmed_edges_and_preserves_interior_pause(self):
        envelope = [-90] * 10 + [-20] * 20 + [-90] * 20 + [-25] * 10 + [-90] * 15
        start, end = ta.audio_boundaries_from_envelope(envelope)
        self.assertAlmostEqual(start, 1.0)
        self.assertAlmostEqual(end, 6.0)

    def test_short_noise_spike_does_not_become_content(self):
        envelope = [-90] * 10 + [-10] + [-90] * 10
        self.assertEqual(ta.audio_boundaries_from_envelope(envelope), (None, None))

    def test_quiet_but_meaningful_audio_above_floor_is_preserved(self):
        envelope = [-90] * 5 + [-50] * 10 + [-90] * 5
        self.assertEqual(ta.audio_boundaries_from_envelope(envelope), (0.5, 1.5))

    def test_fully_silent_and_invalid_envelopes_are_unknown(self):
        self.assertEqual(ta.audio_boundaries_from_envelope([-90] * 50), (None, None))
        self.assertEqual(ta.audio_boundaries_from_envelope([], hop_seconds=0), (None, None))

    def test_detects_a_sustained_outro_fade_but_not_a_hard_end(self):
        fade = [-12, -13, -14, -16, -18, -21, -25, -31, -40, -60]
        start, confidence = ta.estimate_fade_out_from_envelope(
            fade, hop_seconds=0.5, minimum_fade_seconds=2.0,
        )
        self.assertIsNotNone(start)
        self.assertGreaterEqual(confidence, 0.65)
        self.assertEqual(
            ta.estimate_fade_out_from_envelope([-12] * 20, hop_seconds=0.5),
            (None, 0.0),
        )


class EffectiveEndTests(unittest.TestCase):
    def record(self, **changes):
        values = dict(
            path="/tmp/song.mp4", mtime=1, size=2, media_kind="mp4",
            duration=242.0, audio_end=236.4, visual_end=240.1,
            visual_confidence=0.95,
        )
        values.update(changes)
        return ta.TransitionAnalysis(**values)

    def test_waits_for_visual_end_plus_margin(self):
        record = self.record()
        self.assertAlmostEqual(ta.calculate_effective_karaoke_end(record), 240.4)
        self.assertTrue(record.safe_for_early_completion)

    def test_missing_or_uncertain_visual_end_is_never_safe(self):
        for record in (self.record(visual_end=None), self.record(visual_confidence=0.5)):
            self.assertIsNone(ta.calculate_effective_karaoke_end(record))
            self.assertFalse(record.safe_for_early_completion)
            self.assertEqual(record.safety_reason, "visual_end_unverified")

    def test_missing_audio_end_is_never_safe(self):
        record = self.record(audio_end=None)
        self.assertIsNone(ta.calculate_effective_karaoke_end(record))
        self.assertFalse(record.safe_for_early_completion)

    def test_boundary_at_container_end_uses_normal_eos(self):
        record = self.record(visual_end=241.9)
        self.assertEqual(ta.calculate_effective_karaoke_end(record), 242.0)
        self.assertFalse(record.safe_for_early_completion)
        self.assertEqual(record.safety_reason, "normal_eos")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / "song.mp3"
        self.media.write_bytes(b"audio")
        signature = ta.file_signature(str(self.media))
        self.record = ta.TransitionAnalysis(
            path=str(self.media), mtime=signature[0], size=signature[1],
            media_kind="bgm", duration=10.0, envelope_db=[-120, -20, 20],
            audio_start=0.1, audio_end=9.7,
        )
        self.cache_path = Path(self.temp.name) / "transition-analysis.json"

    def test_round_trip_and_compact_quantized_envelope(self):
        cache = ta.TransitionAnalysisCache(self.cache_path)
        cache.put(self.record)
        cache.save()
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        row = payload["records"][str(self.media)]
        self.assertEqual(row["envelope_db"], [-96, -20, 6])
        loaded = ta.TransitionAnalysisCache(self.cache_path)
        loaded.load()
        self.assertEqual(loaded.get(str(self.media)).audio_end, 9.7)

    def test_karaoke_cache_omits_raw_envelope(self):
        self.record.media_kind = "karaoke"
        cache = ta.TransitionAnalysisCache(self.cache_path)
        cache.put(self.record)
        cache.save()
        row = json.loads(self.cache_path.read_text(encoding="utf-8"))["records"][str(self.media)]
        self.assertEqual(row["envelope_db"], [])

    def test_compact_karaoke_builder_keeps_only_derived_edges(self):
        record = ta.build_karaoke_transition_analysis(
            path=str(self.media), duration=10.0, audio_start=0.4, audio_end=9.2,
            integrated_lufs=-15.0, peak_db=-1.2,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.envelope_db, [])
        self.assertEqual(record.audio_start, 0.4)

    def test_changed_file_invalidates_record(self):
        cache = ta.TransitionAnalysisCache(self.cache_path)
        cache.put(self.record)
        self.media.write_bytes(b"changed audio")
        self.assertIsNone(cache.get(str(self.media)))

    def test_old_analysis_version_is_not_loaded(self):
        payload = {
            "records": {
                str(self.media): {**self.record.to_dict(), "analysis_version": 0},
            }
        }
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        cache = ta.TransitionAnalysisCache(self.cache_path)
        cache.load()
        self.assertIsNone(cache.get(str(self.media)))

    def test_malformed_cache_fails_closed(self):
        self.cache_path.write_text("not json", encoding="utf-8")
        cache = ta.TransitionAnalysisCache(self.cache_path)
        cache.load()
        self.assertIsNone(cache.get(str(self.media)))

    def test_impossible_cached_timestamps_fail_closed(self):
        for changes in (
            {"audio_start": 8.0, "audio_end": 2.0},
            {"visual_end": 99.0},
            {"effective_karaoke_end": -1.0},
            {"visual_confidence": 4.0},
        ):
            payload = self.record.to_dict()
            payload.update(changes)
            self.assertIsNone(ta.TransitionAnalysis.from_dict(payload))

    def test_audio_builder_is_additive_and_uses_existing_loudness_values(self):
        record = ta.build_audio_transition_analysis(
            path=str(self.media), media_kind="bgm", duration=2.0,
            envelope_db=[-90] * 3 + [-20] * 12 + [-90] * 5,
            integrated_lufs=-15.2, peak_db=-1.1,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.integrated_lufs, -15.2)
        self.assertEqual(record.peak_db, -1.1)
        self.assertAlmostEqual(record.audio_start, 0.3)

    def test_visual_backfill_preserves_existing_audio_and_loudness(self):
        cache = ta.TransitionAnalysisCache(self.cache_path)
        self.record.integrated_lufs = -16.4
        cache.put(self.record)
        visual = ta.VideoVisualAnalysis(
            visual_start=0.0, visual_end=9.2, confidence=0.97,
            method="mp4_thumbnails_v1", safe_for_early_completion=True,
            reason="active_video_then_static_black_tail",
        )
        merged = cache.merge_visual_result(
            path=str(self.media), media_kind="mp4", duration=10.0,
            result=visual,
        )
        self.assertEqual(merged.integrated_lufs, -16.4)
        self.assertEqual(merged.audio_end, 9.7)
        self.assertEqual(merged.visual_end, 9.2)
        self.assertAlmostEqual(merged.effective_karaoke_end, 10.0)

    def test_uncertain_visual_backfill_stores_reason_but_never_authorizes_trim(self):
        cache = ta.TransitionAnalysisCache(self.cache_path)
        result = ta.VideoVisualAnalysis(
            visual_start=0.0, visual_end=None, confidence=0.0,
            method="mp4_thumbnails_v1", safe_for_early_completion=False,
            reason="static_nonblack_final_screen",
        )
        merged = cache.merge_visual_result(
            path=str(self.media), media_kind="mp4", duration=10.0,
            result=result,
        )
        self.assertFalse(merged.safe_for_early_completion)
        self.assertIsNone(merged.visual_end)
        self.assertEqual(merged.safety_reason, "static_nonblack_final_screen")


class CdgVisualTests(unittest.TestCase):
    @staticmethod
    def packets(*packets, tail_seconds=0.0):
        tail = ta._cdg_packet(0, 0) * int(round(tail_seconds * 300.0))
        return b"".join(packets) + tail

    @staticmethod
    def tile(color0=0, color1=1, row=1, column=1, bits=0x3F):
        data = bytes((color0, color1, row, column)) + bytes((bits,)) * 12
        return ta._cdg_packet(9, 6, data)

    @staticmethod
    def clear(color=0):
        return ta._cdg_packet(9, 1, bytes((color,)))

    def test_explicit_clear_followed_by_blank_tail_is_safe(self):
        payload = self.packets(
            self.tile(),
            ta._cdg_packet(0, 0) * 300,
            self.clear(),
            tail_seconds=2.0,
        )
        result = ta.analyze_cdg_visual_bytes(payload)
        self.assertTrue(result.safe_for_early_completion)
        self.assertEqual(result.reason, "explicit_clear_then_stable_blank_tail")
        self.assertIsNotNone(result.visual_end)

    def test_static_final_lyric_fails_closed(self):
        result = ta.analyze_cdg_visual_bytes(self.packets(self.tile(), tail_seconds=5.0))
        self.assertFalse(result.safe_for_early_completion)
        self.assertIsNone(result.visual_end)
        self.assertEqual(result.reason, "static_or_active_nonblank_final_screen")

    def test_redundant_identical_tile_does_not_extend_visual_activity(self):
        tile = self.tile()
        payload = self.packets(tile, ta._cdg_packet(0, 0) * 300, tile, self.clear(), tail_seconds=2.0)
        result = ta.analyze_cdg_visual_bytes(payload)
        self.assertTrue(result.safe_for_early_completion)
        # The repeated identical tile is a no-op; only the later clear changes pixels.
        self.assertAlmostEqual(result.last_change, result.visual_end)

    def test_clear_without_prior_graphics_is_not_proof(self):
        result = ta.analyze_cdg_visual_bytes(self.packets(self.clear(), tail_seconds=5.0))
        self.assertFalse(result.safe_for_early_completion)
        self.assertIsNone(result.visual_end)

    def test_short_blank_tail_falls_back_to_eos(self):
        result = ta.analyze_cdg_visual_bytes(
            self.packets(self.tile(), self.clear(), tail_seconds=0.2),
            minimum_blank_tail_seconds=1.0,
        )
        self.assertFalse(result.safe_for_early_completion)

    def test_scroll_after_clear_makes_tail_ambiguous(self):
        scroll = ta._cdg_packet(9, 20, bytes(16))
        result = ta.analyze_cdg_visual_bytes(
            self.packets(self.tile(), self.clear(), scroll, tail_seconds=2.0)
        )
        self.assertFalse(result.safe_for_early_completion)

    def test_malformed_or_empty_cdg_fails_closed(self):
        result = ta.analyze_cdg_visual_bytes(b"short")
        self.assertFalse(result.safe_for_early_completion)
        self.assertEqual(result.reason, "empty_cdg")


class VideoVisualTests(unittest.TestCase):
    def sample(self, timestamp, luma, difference):
        return ta.VideoFrameSample(timestamp, luma, difference)

    def test_active_video_followed_by_static_black_tail_is_safe(self):
        samples = [
            self.sample(8.0, 0.45, 0.20),
            self.sample(9.0, 0.40, 0.15),
            self.sample(10.0, 0.0, 0.0),
            self.sample(11.0, 0.0, 0.0),
            self.sample(12.0, 0.0, 0.0),
        ]
        result = ta.analyze_video_tail_samples(samples, duration=12.0)
        self.assertTrue(result.safe_for_early_completion)
        self.assertEqual(result.visual_end, 10.0)

    def test_static_nonblack_final_lyric_fails_closed(self):
        samples = [
            self.sample(8.0, 0.4, 0.2),
            self.sample(9.0, 0.4, 0.0),
            self.sample(10.0, 0.4, 0.0),
            self.sample(12.0, 0.4, 0.0),
        ]
        result = ta.analyze_video_tail_samples(samples, duration=12.0)
        self.assertFalse(result.safe_for_early_completion)
        self.assertIsNone(result.visual_end)
        self.assertEqual(result.reason, "static_nonblack_final_screen")

    def test_continuing_video_activity_fails_closed(self):
        samples = [self.sample(value, 0.4, 0.2) for value in (8.0, 9.0, 10.0, 11.0, 12.0)]
        result = ta.analyze_video_tail_samples(samples, duration=12.0)
        self.assertFalse(result.safe_for_early_completion)
        self.assertEqual(result.reason, "video_active_or_uncertain_through_end")

    def test_short_black_tail_falls_back_to_eos(self):
        samples = [self.sample(11.0, 0.4, 0.1), self.sample(11.5, 0.0, 0.0), self.sample(12.0, 0.0, 0.0)]
        result = ta.analyze_video_tail_samples(
            samples, duration=12.0, minimum_black_tail_seconds=1.0,
        )
        self.assertFalse(result.safe_for_early_completion)

    def test_all_black_or_insufficient_samples_are_not_safe(self):
        all_black = [self.sample(10.0, 0.0, 0.0), self.sample(12.0, 0.0, 0.0)]
        self.assertFalse(ta.analyze_video_tail_samples(all_black, duration=12.0).safe_for_early_completion)
        self.assertFalse(ta.analyze_video_tail_samples([], duration=12.0).safe_for_early_completion)

    def test_bundled_libmpv_produces_bounded_thumbnail_metrics(self):
        from libmpv_media_jobs import sample_video_tail_metrics

        fixture = Path(__file__).parent / "test_media" / "singws_mp4_perf_test_1080p.mp4"
        samples = sample_video_tail_metrics(
            str(fixture), duration_seconds=20.0, tail_seconds=2.0,
            frames_per_second=1.0, width=32, height=18, timeout=20.0,
        )
        self.assertGreaterEqual(len(samples), 1)
        self.assertLessEqual(len(samples), 4)
        for sample in samples:
            self.assertEqual(set(sample), {"timestamp", "mean_luma", "difference"})
            self.assertTrue(0.0 <= sample["mean_luma"] <= 1.0)
            self.assertTrue(0.0 <= sample["difference"] <= 1.0)

    def test_offline_mp4_adapter_is_injected_and_fails_closed(self):
        calls = []

        def sampler(path, *, duration_seconds):
            calls.append((path, duration_seconds))
            return [
                {"timestamp": 8.0, "mean_luma": 0.4, "difference": 0.2},
                {"timestamp": 10.0, "mean_luma": 0.0, "difference": 0.0},
                {"timestamp": 12.0, "mean_luma": 0.0, "difference": 0.0},
            ]

        result = ta.analyze_mp4_visual_offline(
            "/tmp/song.mp4", duration=12.0, metric_sampler=sampler,
        )
        self.assertEqual(calls, [("/tmp/song.mp4", 12.0)])
        self.assertTrue(result.safe_for_early_completion)

        failed = ta.analyze_mp4_visual_offline(
            "/tmp/song.mp4", duration=12.0,
            metric_sampler=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
        )
        self.assertFalse(failed.safe_for_early_completion)
        self.assertEqual(failed.reason, "video_decoder_failed")


class BgmPolicyTests(unittest.TestCase):
    def record(self, **changes):
        values = dict(
            path="/tmp/bgm.mp3", mtime=1, size=2, media_kind="bgm",
            duration=240.0, audio_end=240.0, fade_confidence=0.5,
        )
        values.update(changes)
        return ta.TransitionAnalysis(**values)

    def test_missing_metadata_keeps_existing_crossfade(self):
        self.assertEqual(ta.select_bgm_crossfade_seconds(None), (5.0, "metadata_unavailable"))

    def test_verified_dead_tail_advances_promptly(self):
        self.assertEqual(
            ta.select_bgm_crossfade_seconds(self.record(audio_end=235.0)),
            (2.0, "verified_dead_tail"),
        )

    def test_confident_natural_fade_uses_fade_length_with_bounds(self):
        seconds, reason = ta.select_bgm_crossfade_seconds(
            self.record(audio_end=240.0, fade_start=234.0, fade_confidence=0.95)
        )
        self.assertEqual((seconds, reason), (6.0, "natural_fade"))

    def test_hard_ending_uses_short_transition(self):
        self.assertEqual(
            ta.select_bgm_crossfade_seconds(self.record(fade_confidence=0.0)),
            (2.5, "hard_ending"),
        )


class PreparedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / "song.mp4"
        self.media.write_bytes(b"video")

    def test_exact_stable_identity_matches(self):
        identity = ta.PreparedSourceIdentity.capture(str(self.media), "request-123", 7)
        self.assertTrue(identity.matches(
            path=str(self.media), queue_item_id="request-123", generation=7,
        ))

    def test_reorder_generation_or_request_change_invalidates(self):
        identity = ta.PreparedSourceIdentity.capture(str(self.media), "request-123", 7)
        self.assertFalse(identity.matches(
            path=str(self.media), queue_item_id="request-123", generation=8,
        ))
        self.assertFalse(identity.matches(
            path=str(self.media), queue_item_id="request-456", generation=7,
        ))

    def test_modified_or_missing_file_invalidates(self):
        identity = ta.PreparedSourceIdentity.capture(str(self.media), "request-123", 7)
        self.media.write_bytes(b"different video")
        self.assertFalse(identity.matches(
            path=str(self.media), queue_item_id="request-123", generation=7,
        ))
        self.assertIsNone(ta.PreparedSourceIdentity.capture(
            str(self.media) + ".missing", "request-123", 7,
        ))

if __name__ == "__main__":
    unittest.main()
