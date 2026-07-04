import pathlib
import re
import unittest


MAIN_SOURCE = pathlib.Path("0.2.18.1.py").read_text(encoding="utf-8")


def function_source(name: str) -> str:
    pattern = rf"(?ms)^    def {re.escape(name)}\(.*?^    def "
    match = re.search(pattern, MAIN_SOURCE)
    if match:
        return match.group(0).rsplit("\n    def ", 1)[0]
    pattern = rf"(?ms)^    def {re.escape(name)}\(.*?^class "
    match = re.search(pattern, MAIN_SOURCE)
    if match:
        return match.group(0).rsplit("\nclass ", 1)[0]
    raise AssertionError(f"Could not find function {name}")


class PerformanceSafetyTests(unittest.TestCase):
    def test_preview_overlay_refresh_does_not_force_synchronous_repaint(self):
        source = function_source("_refresh_preview_overlay_binding")
        self.assertNotIn(".repaint(", source)
        self.assertIn(".update()", source)

    def test_bgm_pulse_styles_are_cached(self):
        source = function_source("_apply_bg_pulse_style")
        self.assertIn("_bg_player_frame_css_cache", source)
        self.assertIn("_bg_now_playing_kicker_css_cache", source)
        self.assertIn("round(self._bg_pulse_value(), 1)", source)

    def test_debounced_save_timer_uses_playback_safe_wrapper(self):
        self.assertIn("_save_data_timer.timeout.connect(self._save_data_scheduled)", MAIN_SOURCE)
        scheduled = function_source("_save_data_scheduled")
        self.assertIn("karaoke_playing", scheduled)
        self.assertIn("_start_save_data_worker", scheduled)
        worker = function_source("_start_save_data_worker")
        self.assertIn("threading.Thread", worker)
        self.assertIn("singws-save-data", worker)

    def test_queue_rebuild_suspends_repaints(self):
        source = function_source("update_queue_display")
        self.assertIn("queue_display.setUpdatesEnabled(False)", source)
        self.assertIn("queue_display.setUpdatesEnabled(True)", source)

    def test_queue_add_defers_metadata_lookup_during_playback(self):
        source = function_source("_add_song_to_queue")
        self.assertIn("karaoke_playing", source)
        self.assertIn("[QUEUE-ADD] metadata lookup deferred during playback", source)
        self.assertLess(source.index("karaoke_playing"), source.index("self.lookup_display_name(fallback_path)"))

    def test_next_up_prescan_has_disabled_worker_gate(self):
        source = function_source("_schedule_next_up_prescan")
        self.assertIn("_lead_silence_prescan_enabled", source)
        prescan = function_source("_prescan_next_track")
        self.assertIn("_lead_silence_prescan_enabled", prescan)
        self.assertIn("_next_prescan_inflight", prescan)
        gate = function_source("_lead_silence_prescan_enabled")
        self.assertIn("_performance_mode", gate)
        self.assertIn("_safe_mode", gate)
        self.assertIn("end_silence_trim_enabled", gate)

    def test_playback_worker_diagnostics_are_logged(self):
        refresh = function_source("_request_queue_display_refresh")
        self.assertIn("_log_active_background_workers", refresh)
        diag = function_source("_log_active_background_workers")
        self.assertIn("[PLAYBACK-WORKERS]", diag)
        self.assertIn("loudness_inflight", diag)
        self.assertIn("lead_silence_prescan", diag)

    def test_deferred_remote_adds_save_off_thread_during_playback(self):
        source = function_source("_save_deferred_remote_adds")
        self.assertIn("karaoke_playing", source)
        self.assertIn("_start_deferred_remote_save_worker", source)
        worker = function_source("_start_deferred_remote_save_worker")
        self.assertIn("threading.Thread", worker)
        self.assertIn("singws-save-deferred-remote", worker)

    def test_scan_folder_starts_background_worker_before_inline_fallback(self):
        marker = '    def scan_folder(self):\n        """Scan chooser: Quick Update (incremental) or Full Scan."""'
        self.assertIn(marker, MAIN_SOURCE)
        source = MAIN_SOURCE[MAIN_SOURCE.index(marker):]
        source = source[:source.index("\n    def _fmt_mmss")]
        self.assertIn("_start_library_scan_worker", source)
        self.assertLess(source.index("_start_library_scan_worker"), source.index("Instant filename scan"))

    def test_live_state_polish_has_intel_playback_backoff(self):
        source = function_source("_live_state_interval_ms")
        self.assertIn("x86_64", source)
        self.assertIn("return 350", source)
        install = function_source("_install_live_state_polish")
        self.assertIn("self._live_state_interval_ms()", install)

    def test_waitlist_uses_model_backed_view(self):
        self.assertIn("class WaitlistRequestListModel(QAbstractListModel)", MAIN_SOURCE)
        page = function_source("_build_waiting_for_add_page")
        self.assertIn("self.waiting_for_add_model = WaitlistRequestListModel(self)", page)
        self.assertIn("self.waiting_for_add_list = QListView()", page)
        self.assertIn("self.waiting_for_add_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)", page)
        self.assertIn("self.waiting_for_add_list.setTextElideMode(Qt.TextElideMode.ElideNone)", page)
        self.assertIn("self.waiting_for_add_list.setItemDelegate(WrapHeightItemDelegate", page)
        refresh = function_source("_refresh_waiting_for_add_view")
        self.assertIn("waiting_model.setRows(model_rows)", refresh)
        self.assertNotIn("QListWidgetItem(self._waiting_for_add_row_text", refresh)
        sections = function_source("_waiting_for_add_sections")
        self.assertNotIn("_waiting_for_add_active_rotation_rows", MAIN_SOURCE)
        self.assertNotIn('"active_rotation"', sections)
        self.assertNotIn('"active_rotation"', refresh)

    def test_waitlist_nav_uses_pending_acceptance_pulse(self):
        self.assertIn('_build_nav_item("◈", "Waitlist")', MAIN_SOURCE)
        self.assertIn("_waiting_for_add_pulse_timer", MAIN_SOURCE)
        self.assertNotIn("_waiting_for_add_blink_timer", MAIN_SOURCE)
        update = function_source("_update_waiting_for_add_nav_state")
        self.assertIn("_pending_acceptance_count()", update)
        self.assertIn('f"Waitlist ({count})"', update)
        self.assertIn("rgba(22, 163, 74", update)
        self.assertIn("_waiting_for_add_nav_css_cache_key", update)

    def test_singer_history_song_list_uses_model_backed_view(self):
        self.assertIn("class SingerHistorySongListModel(QAbstractListModel)", MAIN_SOURCE)
        self.assertIn("class SingerHistorySongsBuildWorker(QObject)", MAIN_SOURCE)
        model = MAIN_SOURCE[MAIN_SOURCE.index("class SingerHistorySongListModel"):MAIN_SOURCE.index("class SingerHistorySingerListModel")]
        self.assertIn("if rows == self._rows:", model)
        self.assertIn("self.dataChanged.emit", model)
        page = function_source("_build_singer_history_page")
        self.assertIn("self.singer_history_songs_model = SingerHistorySongListModel(self)", page)
        self.assertIn("self.singer_history_songs_list = QListView()", page)
        self.assertIn("self.singer_history_songs_list.doubleClicked.connect", page)
        self.assertIn("self.singer_history_songs_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)", page)
        self.assertIn("self.singer_history_songs_list.setTextElideMode(Qt.TextElideMode.ElideNone)", page)
        self.assertIn("self.singer_history_songs_list.setItemDelegate(WrapHeightItemDelegate", page)
        details = function_source("_update_singer_history_details")
        self.assertIn("SingerHistorySongsBuildWorker", details)
        self.assertIn("worker.moveToThread(thread)", details)
        apply_rows = function_source("_apply_singer_history_song_rows")
        self.assertIn("self.singer_history_songs_model.setRows(rows)", apply_rows)
        self.assertNotIn("QListWidgetItem(self._history_song_display", details)
        selector = function_source("_selected_singer_history_song_key")
        self.assertIn("model.songKeyForIndex(view.currentIndex())", selector)

    def test_singer_history_directory_uses_model_backed_view(self):
        self.assertIn("class SingerHistorySingerListModel(QAbstractListModel)", MAIN_SOURCE)
        self.assertIn("class SingerHistoryDirectoryBuildWorker(QObject)", MAIN_SOURCE)
        self.assertIn("class SingerHistoryBrandChoicesWorker(QObject)", MAIN_SOURCE)
        model = MAIN_SOURCE[MAIN_SOURCE.index("class SingerHistorySingerListModel"):MAIN_SOURCE.index("class SingerHistoryDirectoryBuildWorker")]
        self.assertIn("if rows == self._rows:", model)
        self.assertIn("self.dataChanged.emit", model)
        page = function_source("_build_singer_history_page")
        self.assertIn("self.singer_history_singer_model = SingerHistorySingerListModel(self)", page)
        self.assertIn("self.singer_history_singer_list = QListView()", page)
        self.assertIn("selectionModel().currentChanged.connect", page)
        self.assertIn("self.singer_history_singer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)", page)
        self.assertIn("self.singer_history_singer_list.setTextElideMode(Qt.TextElideMode.ElideNone)", page)
        self.assertIn("self.singer_history_singer_list.setItemDelegate", page)
        refresh = function_source("_refresh_singer_history_view")
        self.assertIn("SingerHistoryDirectoryBuildWorker", refresh)
        self.assertIn("worker.moveToThread(thread)", refresh)
        apply_rows = function_source("_apply_singer_history_directory_rows")
        self.assertIn("self.singer_history_singer_model.setRows(rows)", apply_rows)
        self.assertNotIn("QListWidgetItem(\"\\n\".join(lines))", refresh)
        self.assertNotIn("self.singer_history_singer_list.addItem", refresh)
        selector = function_source("_selected_singer_history_key")
        self.assertIn("model.singerKeyForIndex(view.currentIndex())", selector)

    def test_singer_history_brand_choices_never_scan_library_on_selection(self):
        choices = function_source("_history_brand_choices")
        combo = function_source("_refresh_singer_history_brand_combo")
        self.assertIn("_ensure_history_brand_choices_async()", choices)
        self.assertIn("SingerHistoryBrandChoicesWorker", MAIN_SOURCE)
        self.assertIn("worker.moveToThread(thread)", function_source("_ensure_history_brand_choices_async"))
        self.assertIn("allow_sync_build=False", combo)
        details = function_source("_update_singer_history_details")
        self.assertNotIn("allow_sync_build=True", details)

    def test_karaoke_to_bgm_overlap_requires_explicit_setting(self):
        self.assertIn('"karaoke_bgm_crossfade_enabled": False', MAIN_SOURCE)
        self.assertIn('"karaoke_allow_early_silence_trim": False', MAIN_SOURCE)
        end_handler = function_source("_handle_media_end_safe")
        self.assertIn("self._karaoke_bgm_crossfade_enabled()", end_handler)
        timer_tick = function_source("update_time_left")
        self.assertIn("crossfade_enabled = self._karaoke_bgm_crossfade_enabled()", timer_tick)
        self.assertIn("if (crossfade_enabled", timer_tick)
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("self._karaoke_early_silence_trim_enabled()", trim)
        self.assertIn("self._karaoke_bgm_crossfade_enabled()", trim)
        fade = function_source("_start_bg_with_fade")
        self.assertIn('resume_reason == "karaoke_end_overlap" and self._karaoke_bgm_crossfade_enabled()', fade)

    def test_singer_history_edits_use_debounced_save_path(self):
        source = function_source("_commit_singer_history_change")
        self.assertIn("_schedule_save_data", source)
        self.assertNotIn("self.save_data()", source)

    def test_queue_uses_model_backed_view_with_identity_roles(self):
        self.assertIn("class QueueListModel(QAbstractListModel)", MAIN_SOURCE)
        self.assertIn("class QueueListView(QListView)", MAIN_SOURCE)
        self.assertIn("_queue_row_kind_role", MAIN_SOURCE)
        self.assertIn("def _set_queue_row_identity", MAIN_SOURCE)
        page = MAIN_SOURCE[MAIN_SOURCE.index('self.queue_label = QLabel("Singers:0 (0:00)")'):]
        page = page[:page.index("# --- 1st Row: Move Up / Move Down / Remove ---")]
        self.assertIn("self.queue_display = QueueListView()", page)
        self.assertIn("self.queue_display_model = QueueListModel(self)", page)
        self.assertIn("self.queue_display.setModel(self.queue_display_model)", page)
        refresh = function_source("update_queue_display")
        self.assertIn("model_rows = []", refresh)
        self.assertIn("model.syncRows(model_rows)", refresh)
        self.assertNotIn("model.setRows(model_rows)", refresh)
        self.assertIn('for song_idx, entry in enumerate(singer.get("songs", [])):', refresh)
        self.assertIn('self._set_queue_row_identity(item, "singer", singer_idx, -1)', refresh)
        self.assertIn('self._set_queue_row_identity(item, "song", singer_idx, song_idx)', refresh)
        context = function_source("show_queue_context_menu")
        self.assertIn("kind = self._queue_item_kind(item)", context)
        self.assertIn("self._queue_item_singer_index(item, row)", context)
        self.assertIn("self._queue_item_song_indices(item, row)", context)
        move_up = function_source("move_up")
        move_down = function_source("move_down")
        remove = function_source("remove_selected")
        self.assertIn("current_kind = self._queue_item_kind(current_item)", move_up)
        self.assertIn("current_kind = self._queue_item_kind(current_item)", move_down)
        self.assertIn("item_kind = self._queue_item_kind(item)", remove)
        self.assertIn("_refresh_queue_song_row_identities(singer_idx)", move_up)
        self.assertIn("_refresh_queue_song_row_identities(singer_idx)", move_down)
        self.assertNotIn("takeItem", move_up)
        self.assertNotIn("insertItem", move_up)
        self.assertNotIn("takeItem", move_down)
        self.assertNotIn("insertItem", move_down)

    def test_async_remote_intake_ignores_uninitialized_qobject_stubs(self):
        helper = function_source("_qt_object_is_initialized")
        self.assertIn("self.thread()", helper)
        self.assertIn("except RuntimeError", helper)
        source = function_source("_should_async_remote_request_intake")
        self.assertIn("_qt_object_is_initialized()", source)
        accepting = function_source("_ensure_server_accepting_matches_host_intent_async")
        self.assertIn('getattr(self, "_disable_accepting_watchdog"', accepting)
        self.assertIn("except RuntimeError", accepting)
        waitlist = function_source("_sync_waitlist_state_from_server_async")
        self.assertIn('getattr(self, "_disable_waitlist_state_pull"', waitlist)
        self.assertIn("except RuntimeError", waitlist)


if __name__ == "__main__":
    unittest.main()
