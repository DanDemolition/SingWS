import os
import tempfile
import unittest
from pathlib import Path

import karafun_provider
import song_index
from playback_providers import AvailabilityStatus, SongProvider


class KaraFunProviderTests(unittest.TestCase):
    def test_streaming_reference_becomes_external_track_dict(self):
        ref = karafun_provider.KaraFunReference(
            title="Song",
            artist="Artist",
            provider_track_id="kf-123",
            provider_url="https://www.karafun.com/karaoke/artist/song/",
            streaming=True,
        ).to_provider_track()

        data = ref.to_track_dict()
        self.assertEqual(data["provider"], SongProvider.KARAFUN_STREAMING.value)
        self.assertEqual(data["provider_track_id"], "kf-123")
        self.assertEqual(data["availability_status"], AvailabilityStatus.EXTERNALLY_CONTROLLED.value)
        self.assertTrue(data["path"].startswith("karafun_streaming:"))

    def test_kfn_reference_never_claims_direct_playback(self):
        ref = karafun_provider.kfn_reference("/tmp/Artist - Song.kfn", artist="Artist")

        self.assertEqual(ref.provider, SongProvider.KARAFUN_LOCAL)
        self.assertEqual(ref.availability_status, AvailabilityStatus.EXTERNALLY_CONTROLLED)
        self.assertEqual(ref.local_reference_path, "/tmp/Artist - Song.kfn")

    def test_search_index_preserves_provider_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            tracks_path = Path(td) / "tracks.json"
            db_path = Path(td) / "singws.db"
            tracks_path.write_text(
                """
                [
                  {
                    "artist": "Artist",
                    "title": "Song",
                    "display": "Artist - Song",
                    "path": "karafun_streaming:kf-123",
                    "provider": "karafun_streaming",
                    "provider_track_id": "kf-123",
                    "provider_url": "https://www.karafun.com/karaoke/artist/song/",
                    "authorization_requirement": "karafun_pro_subscription",
                    "availability_status": "externally_controlled"
                  }
                ]
                """,
                encoding="utf-8",
            )

            rows, _elapsed = song_index.rebuild_from_tracks_json(tracks_path, db_path, verbose=False)
            self.assertEqual(rows, 1)

            hits = song_index.search_songs("kf-123", dbfile=db_path)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["provider"], "karafun_streaming")
            self.assertEqual(hits[0]["provider_track_id"], "kf-123")
            self.assertEqual(hits[0]["availability_status"], "externally_controlled")

    def test_assisted_workflow_manages_the_macos_show_screen(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"karafun_manage_show_screen": True', source)
        self.assertIn('"karafun_transparent_handoff": True', source)
        self.assertIn("def _handoff_show_screen_to_karafun(self):", source)
        self.assertIn("vw.setWindowOpacity(0.0)", source)
        self.assertIn('_karafun_transparent_renderer_ready", False', source)
        self.assertIn('state.get("mode") == "transparent"', source)
        self.assertIn('state.get("window_opacity", 1.0)', source)
        self.assertIn("NSWindowCollectionBehaviorFullScreenAuxiliary", source)
        self.assertIn("NSWindowCollectionBehaviorCanJoinAllSpaces", source)
        self.assertIn("NSWindowCollectionBehaviorFullScreenPrimary", source)
        self.assertIn("NSWindowStyleMaskBorderless", source)
        self.assertIn("native_window.setIgnoresMouseEvents_(True)", source)
        self.assertIn("native_window.setIgnoresMouseEvents_(False)", source)
        self.assertIn("def _after_transparent_karafun_hidden", source)
        self.assertIn('return "MINIMIZED"', source)
        self.assertIn('return "MINIMIZE_FAILED"', source)
        self.assertIn("native_window.setStyleMask_", source)
        self.assertIn("native_window.setCollectionBehavior_", source)
        self.assertIn("NSApplication.sharedApplication().activateIgnoringOtherApps_(True)", source)
        self.assertIn('_restore_transparent_singws("karafun_hidden")', source)
        self.assertIn('_restore_transparent_singws("fallback_timeout")', source)
        self.assertIn("revealed KaraFun true_fullscreen=", source)
        self.assertIn("fullscreen auxiliary", source)
        self.assertIn("vw.setWindowState(Qt.WindowState.WindowNoState)", source)
        self.assertIn("vw.showNormal()", source)
        self.assertIn("vw.hide()", source)
        self.assertNotIn("vw.showMinimized()", source)
        self.assertIn('attribute "AXFullScreen"', source)
        self.assertIn('labelText contains "dual"', source)
        self.assertIn('labelText contains "renderer"', source)
        self.assertIn("bestButtonX", source)
        self.assertIn("click at {bestButtonX, bestButtonY}", source)
        self.assertNotIn("FAST_READY", source)
        self.assertNotIn("show-screen fast handoff", source)
        self.assertNotIn('return "MISS|geometry"', source)
        self.assertIn('return "NO_DUAL_RENDERER"', source)
        self.assertIn("leaving KaraFun control window unchanged", source)
        self.assertNotIn('set value of attribute "AXFullScreen" of controlWindow to true', source)
        self.assertNotIn("control window fullscreen fallback confirmed", source)
        self.assertIn('name of candidateWindow is "Dual Renderer"', source)
        self.assertNotIn("set outputWindow to last window", source)
        self.assertIn("def _macos_native_double_click", source)
        self.assertIn("CGEventSetIntegerValueField", source)
        self.assertIn("def _finish_handoff(result=\"\"):", source)
        self.assertIn('== "READY"', source)
        self.assertIn("Dual Renderer fullscreen verified", source)
        self.assertIn('set lastState to "WINDOWED"', source)
        self.assertIn("_double_click_and_verify", source)
        self.assertIn("Dual Renderer still windowed; retrying double-click", source)
        self.assertIn("Dual Renderer unavailable; retrying recreation", source)
        self.assertIn("The renderer can temporarily disappear from AX", source)
        self.assertIn("'repeat 30 times'", source)
        self.assertIn("'return lastState'", source)
        self.assertIn("timeout=6", source)
        self.assertIn("QTimer.singleShot(400, _double_click_and_verify)", source)
        self.assertIn("QTimer.singleShot(250, lambda: _fullscreen_karafun(attempt + 1))", source)
        self.assertIn("_fullscreen_karafun(attempt + 1)", source)
        self.assertIn("self._run_on_ui_thread(_deliver_completion)", source)
        self.assertIn("_karafun_handoff_token", source)
        self.assertIn("show-screen handoff already in progress; duplicate ignored", source)
        self.assertIn("skipped stale show-screen handoff result", source)
        self.assertIn('_schedule_early_handoff("before_result_activation")', source)
        self.assertIn("fullscreen audience handoff ready before play", source)
        self.assertLess(
            source.index('_schedule_early_handoff("before_result_activation")'),
            source.index("activating KaraFun result mode="),
        )
        handoff = source[source.index("def _handoff_show_screen_to_karafun"):]
        handoff = handoff[:handoff.index("def _restore_show_screen_from_karafun")]
        self.assertEqual(handoff.count("click at {bestButtonX, bestButtonY}"), 2)
        self.assertIn('if outputWindow is not missing value then', handoff)
        self.assertIn('if dualStillOpen then return "DUAL_DID_NOT_CLOSE"', handoff)
        self.assertIn("KaraFun debounces this toggle", handoff)
        self.assertIn("'delay 0.8'", handoff)
        self.assertIn("create a fresh renderer", handoff)
        self.assertNotIn('perform action "AXPress" of bestButton', handoff)
        self.assertNotIn("'click bestButton'", handoff)
        fallback = handoff[handoff.index("def _fullscreen_karafun"):]
        self.assertNotIn('set value of attribute "AXFullScreen" of outputWindow to true', fallback)
        self.assertNotIn("set outputWindow to first window", fallback)
        self.assertIn("def _restore_show_screen_from_karafun(self):", source)
        self.assertIn('attribute "AXMinimized"', source)
        restore = source[source.index("def _restore_show_screen_from_karafun"):]
        restore = restore[:restore.index("def _copy_karafun_lookup_text")]
        self.assertIn("restore_token = uuid.uuid4().hex", restore)
        self.assertIn("skipped stale KaraFun Dual Renderer minimize", restore)
        self.assertIn("skipped KaraFun Dual Renderer minimize completion during active playback", restore)
        self.assertIn('if name of w is "Dual Renderer" then set outputWindow to w', restore)
        self.assertIn('set value of attribute "AXMinimized" of outputWindow to true', restore)
        self.assertNotIn('set value of attribute "AXMinimized" of w to true', restore)
        self.assertNotIn('set value of attribute "AXFullScreen" of w to false', restore)
        self.assertIn("on_complete=_after_karafun_hidden", restore)
        self.assertIn("timeout=3", restore)
        self.assertIn('was_fullscreen = bool(state.get("fullscreen", False))', restore)
        self.assertIn('was_maximized = bool(state.get("maximized", False))', restore)
        self.assertIn("restore_fullscreen = was_fullscreen or was_maximized", restore)
        self.assertNotIn("vw.showMaximized()", restore)
        self.assertIn("skipped duplicate SingWS show-screen restore", restore)
        self.assertIn("vw.showFullScreen()", source)
        self.assertIn("Qt.WindowState.WindowNoState", source)
        self.assertIn("_enter_singws_fullscreen", source)
        self.assertIn("duration_watchdog = threading.Timer", source)
        self.assertIn("duration_watchdog.daemon = True", source)
        self.assertIn("reason=duration_watchdog", source)

        start = source[source.index("def _start_external_karafun_playback"):]
        start = start[:start.index("def _show_external_karafun_dialog")]
        self.assertNotIn("self._handoff_show_screen_to_karafun()", start)
        self.assertIn("self._automate_karafun_search_and_play(entry, key=key, tempo_percent=tempo_percent)", start)

        finish = source[source.index("def _finish_external_karafun_playback"):]
        finish = finish[:finish.index("def play_next_file")]
        self.assertIn("self._restore_show_screen_from_karafun()", finish)

    def test_network_settings_keep_karafun_link_machine_local(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"karafun_request_url": ""', source)
        self.assertIn('QLabel("KaraFun Integration")', source)
        self.assertIn('QCheckBox("Enable automatic KaraFun queueing on this Mac")', source)
        self.assertIn('self.settings["karafun_request_url"] = karafun_url_edit.text().strip()', source)
        self.assertIn("karafun_url_edit.setEchoMode(QLineEdit.EchoMode.Password)", source)
        self.assertNotIn('"karafun_request_url": "https://', source)

    def test_location_permission_is_once_and_karafun_restore_has_fallback(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"session_location_permission_requested": False', source)
        self.assertIn('not bool(self.settings.get("session_location_permission_requested", False))', source)
        self.assertNotIn('lat_raw == "" or lng_raw == "" or source == "auto_detected"', source)
        restore = source[source.index("def _restore_show_screen_from_karafun"):]
        restore = restore[:restore.index("def _copy_karafun_lookup_text")]
        self.assertIn("restore snapshot missing; using current show-screen display", restore)
        self.assertNotIn("self._macos_native_double_click(", restore)
        self.assertIn("skipped SingWS show-screen restore during active KaraFun playback", restore)
        self.assertIn('QTimer.singleShot(250, lambda: _restore_singws("karafun_hidden"))', restore)
        self.assertIn('QTimer.singleShot(3500, lambda: _restore_singws("fallback_timeout"))', restore)

    def test_external_start_wires_exact_search_play_and_adjustment_worker(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("from AppKit import NSAppleScript", source)
        self.assertIn("def _run_karafun_applescript_sync", source)
        search_helper = source[source.index("def _karafun_search_script"):]
        search_helper = search_helper[:search_helper.index("def _automate_karafun_search_only")]
        worker = source[source.index("def _automate_karafun_search_and_play"):]
        worker = worker[:worker.index("def _fade_bg_for_external_karafun")]
        self.assertIn('entry["karafun_submission_state"] = "karafun_pending"', worker)
        self.assertIn('entry["karafun_pending_at"] = time.time()', worker)
        self.assertIn('name of candidateWindow is not "Dual Renderer"', search_helper)
        self.assertIn("repeat 80 times", search_helper)
        self.assertIn('labelText contains "catalog"', search_helper)
        self.assertIn("fallbackField", search_helper)
        self.assertIn('key code 0 using command down', search_helper)
        self.assertIn("def _karafun_applescript_literal", source)
        self.assertIn("query_literal = self._karafun_applescript_literal(query)", search_helper)
        self.assertIn("safe_title_literal = self._karafun_applescript_literal(safe_title)", search_helper)
        self.assertIn("keystroke {query_literal}", search_helper)
        self.assertIn("(name of elem as text) contains {safe_title_literal}", search_helper)
        self.assertIn('if durationText is not "" then return "FIRST|"', search_helper)
        self.assertIn('if (n is not "") and (centerX < cutoff) and (centerY > ((item 2 of wp) + 110)) then', search_helper)
        self.assertIn("durationText", search_helper)
        self.assertIn('return "FOUND|" & rowX & "|" & rowY & "|" & durationText', search_helper)
        self.assertIn('return "FIRST|" & centerX & "|" & centerY & "|" & durationText', search_helper)
        self.assertIn("def _karafun_search_queries_for_entry", source)
        self.assertIn("search_queries = self._karafun_search_queries_for_entry(entry)", worker)
        self.assertIn("for attempt, query in enumerate(search_queries, start=1):", worker)
        self.assertIn("search_script = self._karafun_search_script(", worker)
        self.assertIn('len(parts) < 3 or parts[0] not in {"FOUND", "FIRST"}', worker)
        self.assertIn('_apply_verified_duration(entry, selected_duration, source="karafun_result")', worker)
        self.assertIn("replaced_estimate=1", worker)
        self.assertIn("selected duration seconds=", worker)
        self.assertIn('entry["karafun_result_activated_at"] = result_activated_at', worker)
        self.assertIn('entry["karafun_playback_clock_started_at"] = result_activated_at', worker)
        self.assertIn('entry["karafun_playback_clock_started_at"] = time.monotonic()', worker)
        self.assertIn("set wp to position of mainWindow", worker)
        self.assertIn("set ws to size of mainWindow", worker)
        self.assertIn("set playX to (item 1 of wp) + ((item 1 of ws) * 0.768)", worker)
        self.assertIn("set playY to (item 2 of wp) + ((item 2 of ws) * 0.222)", worker)
        self.assertIn('return "PLAY|"', worker)
        self.assertNotIn('return "PLAY_NEXT|"', worker)
        self.assertIn("_macos_native_mouse_click", worker)
        self.assertIn("playback pre-click state=", worker)
        self.assertIn("prepare_renderer_script", worker)
        self.assertIn('descriptionText contains "Dual Renderer"', worker)
        self.assertIn('helpText contains "Dual-Screen Display"', worker)
        self.assertIn('if value of attribute "AXFullScreen" of outputWindow then return "READY"', worker)
        self.assertIn('self._karafun_transparent_renderer_ready = True', worker)
        self.assertIn("using legacy show-screen handoff", worker)
        self.assertIn('controlDescription is "pause" or controlDescription is "stop"', worker)
        self.assertLess(worker.index('if playingHintFound then return "PLAYING"'), worker.index('if idleTextFound then return "IDLE"'))
        self.assertIn("play click skipped already playing", worker)
        self.assertIn("playback verify attempt=", worker)
        self.assertIn("def _schedule_early_handoff", worker)
        self.assertIn("def _schedule_bgm_fade", worker)
        self.assertIn('_schedule_bgm_fade("before_fullscreen_handoff")', worker)
        self.assertIn("delayed BGM fade scheduled reason=", worker)
        self.assertLess(
            worker.index('_schedule_bgm_fade("before_fullscreen_handoff")'),
            worker.index("activating KaraFun result mode="),
        )
        self.assertIn('_schedule_early_handoff("pre_click_playing")', worker)
        self.assertIn('_schedule_early_handoff("playback_verified")', worker)
        self.assertIn("self._run_on_ui_thread(self._handoff_show_screen_to_karafun)", worker)
        self.assertIn('verified_playing = bool(ok and initial_probe_state == "PLAYING")', worker)
        self.assertIn("if not verified_playing:", worker)
        self.assertIn('while bool(getattr(self, "_karafun_handoff_in_progress", False))', worker)
        self.assertIn("KaraFun did not report active playback after Play", worker)
        self.assertIn('adjustment_signature = f"key={requested_key};tempo={requested_tempo}"', source)
        self.assertIn("needs_adjustment = requested_key != 0 or requested_tempo != 100", worker)
        self.assertIn('entry.get("karafun_adjustment_applied")', worker)
        self.assertIn('entry["karafun_adjustment_applied"] = adjustment_signature', worker)
        self.assertIn("adjustment skipped already applied", worker)
        self.assertIn("adjustment skipped default key/tempo", worker)
        self.assertIn('labelText contains "key"', worker)
        self.assertIn('labelText contains "tempo"', worker)
        self.assertIn('if (role of elem is "AXButton") and (help of elem is "Audio Settings") then click elem', worker)
        self.assertIn("self._run_karafun_applescript_sync(search_script", worker)
        self.assertIn("self._run_karafun_applescript_sync(play_script", worker)
        self.assertIn('entry["karafun_play_started_at"] = time.time()', worker)
        self.assertIn('entry["karafun_submission_state"] = "karafun_queued"', worker)
        self.assertIn("self._handoff_show_screen_to_karafun()", worker)
        start = source[source.index("def _start_external_karafun_playback"):]
        start = start[:start.index("def _show_external_karafun_dialog")]
        self.assertNotIn("self._handoff_show_screen_to_karafun()", start)
        self.assertIn('if not automatic_playback:', start)
        self.assertIn('if open_automatically and not automatic_playback:', start)
        self.assertIn('if automatic_playback:', start)
        self.assertIn("self._automate_karafun_search_and_play(entry, key=key, tempo_percent=tempo_percent)", start)

    def test_karafun_monitor_completes_once_with_five_seconds_remaining(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        monitor = source[source.index("def _start_karafun_completion_monitor"):]
        monitor = monitor[:monitor.index("def _fade_bg_for_external_karafun")]
        self.assertIn('name of candidateWindow is not "Dual Renderer"', monitor)
        self.assertIn("if mainWindow is missing value then return", monitor)
        self.assertIn("last_clock_candidates = {}", monitor)
        self.assertIn("next_clock_candidates = {}", monitor)
        self.assertIn("current <= previous", monitor)
        self.assertIn("0 <= current < total", monitor)
        self.assertIn("for i in range(0, max(0, len(clocks) - 1))", monitor)
        self.assertIn("current, total = clocks[i], clocks[i + 1]", monitor)
        self.assertIn("remaining <= 5 and age > 8.0", monitor)
        self.assertIn("STATE|IDLE", monitor)
        self.assertIn('if idleTextFound and not playingHintFound then set out to out & "STATE|IDLE"', monitor)
        self.assertIn('if playingHintFound then set out to out & "STATE|PLAYING"', monitor)
        self.assertIn("idle_stop_count = 0", monitor)
        self.assertIn("idle_stop_count += 1", monitor)
        self.assertIn("idle_stop_count >= 2 and age > 8.0", monitor)
        self.assertIn('entry.get("karafun_playback_clock_started_at")', monitor)
        self.assertIn("fallback_duration={fallback_duration} clock_age=", monitor)
        self.assertIn("remaining_from_fallback = True", monitor)
        self.assertIn('reason = "duration_fallback" if remaining_from_fallback else "karaFun_idle"', monitor)
        self.assertIn("completion event received reason=", monitor)
        self.assertIn("duration_fallback", monitor)
        self.assertIn("NSAppleScript calls into KaraFun are not safely concurrent", monitor)
        self.assertIn('if bool(getattr(self, "_karafun_handoff_in_progress", False)):', monitor)
        self.assertIn('self._finish_external_karafun_playback("complete")', monitor)
        self.assertIn("karafun_completion_monitor", monitor)
        automation = source[source.index("def _automate_karafun_search_and_play"):]
        automation = automation[:automation.index("def _karafun_clock_seconds")]
        self.assertIn("self._start_karafun_completion_monitor(entry)", automation)

    def test_karafun_completion_restores_singws_and_resumes_bgm(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        finish = source[source.index("def _finish_external_karafun_playback"):]
        finish = finish[:finish.index("def play_next_file")]
        self.assertIn("self._restore_show_screen_from_karafun()", finish)
        self.assertIn('self._set_karafun_entry_status(entry, "completed"', finish)
        self.assertIn('entry["karafun_submission_state"] = "karafun_completed"', finish)
        self.assertIn("self._record_singer_history_play(", finish)
        self.assertIn('self._sync_singer_history_async("external_karafun_complete")', finish)
        self.assertIn('self._mark_next_up_overlay_pending_after_completion(reason="external_karafun_complete")', finish)
        self.assertIn('self._schedule_bg_resume(120, reason="external_karafun_complete")', finish)
        self.assertIn("self.post_rotation()", finish)
        self.assertIn("rotation advanced after completion", finish)

    def test_manual_karafun_add_only_requires_singer_artist_title(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        manual = source[source.index("def add_karafun_reference_to_queue"):]
        manual = manual[:manual.index("def _build_karafun_streaming_track")]
        self.assertIn('("Singer", singer_edit)', manual)
        self.assertIn('("Artist", artist_edit)', manual)
        self.assertIn('("Title", title_edit)', manual)
        self.assertNotIn("url_edit", manual)
        self.assertNotIn("track_id_edit", manual)
        self.assertNotIn("URL / File", manual)
        self.assertNotIn("Track ID", manual)

        builder = source[source.index("def _build_karafun_streaming_track"):]
        builder = builder[:builder.index("def _on_search_result_double_clicked")]
        self.assertIn('reference_id = provider_track_id or provider_url or f"manual:{uuid.uuid4().hex}"', builder)
        self.assertNotIn("if not provider_track_id:", builder)

    def test_manual_karafun_add_does_not_search_until_play(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        manual = source[source.index("def add_karafun_reference_to_queue"):]
        manual = manual[:manual.index("def _build_karafun_streaming_track")]
        self.assertNotIn("_find_queue_entry_by_song_path(song_path)", manual)
        self.assertNotIn("_automate_karafun_search_only(entry)", manual)

        search_only = source[source.index("def _automate_karafun_search_only"):]
        search_only = search_only[:search_only.index("def _automate_karafun_search_and_play")]
        self.assertIn("Searching KaraFun", search_only)
        self.assertIn("KaraFun search complete", search_only)
        self.assertNotIn("Play Next", search_only)
        self.assertNotIn("_start_karafun_completion_monitor", search_only)

    def test_karafun_accessibility_denial_gets_permission_message(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("def _is_karafun_accessibility_error", source)
        self.assertIn("not allowed assistive access", source)
        self.assertIn("Privacy_Accessibility", source)
        self.assertIn("KARAFUN PERMISSION", source)

        search_only = source[source.index("def _automate_karafun_search_only"):]
        search_only = search_only[:search_only.index("def _automate_karafun_search_and_play")]
        self.assertIn("self._is_karafun_accessibility_error", search_only)
        self.assertIn("self._show_karafun_accessibility_setup", search_only)
        self.assertIn('"permission"', search_only)

        play_worker = source[source.index("def _automate_karafun_search_and_play"):]
        play_worker = play_worker[:play_worker.index("def _karafun_clock_seconds")]
        self.assertIn("self._is_karafun_accessibility_error", play_worker)
        self.assertIn("self._show_karafun_accessibility_setup", play_worker)

    def test_karafun_copy_lookup_text_is_not_stranded_in_completion_monitor(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        copy_helper = source[source.index("def _copy_karafun_lookup_text"):]
        copy_helper = copy_helper[:copy_helper.index("def _automate_karafun_search_and_play")]
        self.assertIn("QApplication.clipboard().setText(text)", copy_helper)
        self.assertIn("return True", copy_helper)

        monitor = source[source.index("def _start_karafun_completion_monitor"):]
        monitor = monitor[:monitor.index("def _fade_bg_for_external_karafun")]
        self.assertNotIn("QApplication.clipboard().setText(text)", monitor)

    def test_server_catalog_request_uses_manual_karafun_track_shape(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("def _build_karafun_streaming_track", source)
        prepare = source[source.index("def _prepare_remote_request_add_payload"):]
        prepare = prepare[:prepare.index("def _apply_resolved_remote_add")]
        self.assertIn('selected_source in {"karafun", "karafun_streaming", "external_karafun"}', prepare)
        self.assertIn('provider_track_id.lower().startswith("kf_")', prepare)
        self.assertIn("self._build_karafun_streaming_track(", prepare)
        self.assertIn('"song_data": (track["path"], key, tempo_percent)', prepare)
        self.assertIn("local_mp4_matches", prepare)
        self.assertIn("local_matches = []", prepare)
        self.assertIn('{".mp4", ".mp3", ".cdg", ".zip"}', prepare)
        self.assertIn("os.path.isfile", prepare)
        self.assertGreater(prepare.index("return _external_karafun_payload()"), prepare.index("self._find_song_for_request"))

    def test_online_catalog_search_is_opt_in_and_badged(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"karafun_include_online_search": False', source)
        self.assertIn('QCheckBox("Include online KaraFun catalog results in SingWS search")', source)
        self.assertIn('self.settings["karafun_include_online_search"]', source)
        self.assertIn("def _karafun_rows(self, local_rows: list)", source)
        self.assertIn('str(row.get("source") or "").strip().lower() != "karafun"', source)
        self.assertIn('"karafun_catalog_only": True', source)
        self.assertIn('provider_badge = "KaraFun ONLINE"', source)
        self.assertIn('media_badge = "" if karafun_catalog_only', source)
        self.assertNotIn('"disc_id": track_id,', source)
        self.assertIn('"/karafun_search.php"', source)

    def test_network_settings_scroll_with_fixed_save_buttons(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        network = source[source.index("def configure_network(self):"):]
        network = network[:network.index("def _friendly_location_detection_error")]
        self.assertIn("scroll = QScrollArea(dlg)", network)
        self.assertIn("scroll.setWidgetResizable(True)", network)
        self.assertIn("outer.addWidget(scroll, 1)", network)
        self.assertIn("outer.addWidget(btns)", network)
        self.assertNotIn("v.addWidget(btns)", network)

    def test_full_scan_rebuilds_while_update_is_incremental_and_routine_text_is_quiet(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        scan = source[source.index("def _build_library_scan_result"):]
        scan = scan[:scan.index("class LibraryScanWorker")]
        self.assertIn('"duration": old_duration_by_path.get(full_path) if quick_mode else None', scan)
        chooser = source[source.index("def _legacy_scan_folder(self):"):]
        chooser = chooser[:chooser.index("def search_tracks(self):")]
        self.assertIn("Update checks saved folders", chooser)
        self.assertIn("Full Scan rereads the entire selected library", chooser)
        self.assertIn("def scan_folder(self):\n        self.open_library_locations_dialog()", source)
        commit = source[source.index("def _commit_undoable_action"):]
        commit = commit[:commit.index("def _update_undo_action")]
        self.assertNotIn("_show_processing_notification", commit)
        self.assertIn("def _clear_routine_processing_text_for_playback", source)


if __name__ == "__main__":
    unittest.main()
