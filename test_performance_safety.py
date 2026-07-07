import pathlib
import re
import unittest
import importlib.util

from PyQt6.QtGui import QColor, QImage


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
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("singws_main_perf", "0.2.18.1.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.singws = module

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
        self.assertIn("end_silence_trim_enabled", gate)
        self.assertNotIn("_performance_mode", gate)
        self.assertNotIn("_safe_mode", gate)

    def test_playback_worker_diagnostics_are_logged(self):
        refresh = function_source("_request_queue_display_refresh")
        self.assertIn("_log_active_background_workers", refresh)
        diag = function_source("_log_active_background_workers")
        self.assertIn("[PLAYBACK-WORKERS]", diag)
        self.assertIn("loudness_inflight", diag)
        self.assertIn("lead_silence_prescan", diag)

    def test_logging_setup_does_not_duplicate_handlers(self):
        source = MAIN_SOURCE[MAIN_SOURCE.index("def setup_logging"):MAIN_SOURCE.index("# Initialize logging")]
        self.assertIn("_singws_file_handler", source)
        self.assertIn("_singws_console_handler", source)
        self.assertIn("any(bool(getattr(h, \"_singws_file_handler\", False))", source)
        self.assertIn("any(bool(getattr(h, \"_singws_console_handler\", False))", source)
        self.assertLess(source.index("_singws_file_handler"), source.index("logger.addHandler(file_handler)"))
        self.assertLess(source.index("_singws_console_handler"), source.index("logger.addHandler(console_handler)"))

    def test_packaged_app_has_opt_in_smoke_exit(self):
        main_block = MAIN_SOURCE[MAIN_SOURCE.index('if __name__ == "__main__":'):]
        self.assertIn("SINGWS_SMOKE_EXIT_MS", main_block)
        self.assertIn("QTimer.singleShot(smoke_exit_ms, window.close)", main_block)
        self.assertIn("[SMOKE] scheduled app exit", main_block)

    def test_host_control_state_sync_timer_starts_on_ui_thread(self):
        source = function_source("_schedule_host_control_state_sync")
        self.assertIn("QThread.currentThread() != app.thread()", source)
        self.assertIn("self._run_on_ui_thread(self._schedule_host_control_state_sync)", source)
        self.assertLess(source.index("QThread.currentThread()"), source.index("timer.start("))

    def test_ticker_debounce_timer_starts_on_ui_thread(self):
        source = function_source("schedule_ticker_update")
        self.assertIn("QThread.currentThread() != app.thread()", source)
        self.assertIn("self._run_on_ui_thread(self.schedule_ticker_update)", source)
        self.assertLess(source.index("QThread.currentThread()"), source.index("self._ticker_update_timer.start(150)"))
        queue_refresh = function_source("update_queue_display")
        self.assertIn("_owner_thread = self.thread()", queue_refresh)
        self.assertIn("_QThread.currentThread() != _owner_thread", queue_refresh)
        self.assertIn("self._finish_queue_display_refresh_side_effects()", queue_refresh)
        side_effects = function_source("_finish_queue_display_refresh_side_effects")
        self.assertIn("owner_thread = self.thread()", side_effects)
        self.assertIn("self._run_on_ui_thread(self._finish_queue_display_refresh_side_effects)", side_effects)
        self.assertIn("QThread.currentThread() == timer.thread()", side_effects)
        self.assertIn("timer.start(10000)", side_effects)
        rotation_view = function_source("update_rotation_view")
        self.assertIn("owner_thread = self.rotation_view.thread()", rotation_view)
        self.assertIn("self._run_on_ui_thread(self.update_rotation_view)", rotation_view)

    def test_websocket_relay_lifecycle_stays_on_ui_thread(self):
        transport_setting = function_source("_request_transport_setting")
        self.assertIn('os.environ.get("SINGWS_REQUEST_TRANSPORT"', transport_setting)
        self.assertIn('if value == "polling":', transport_setting)
        for name in (
            "_start_request_relay",
            "_stop_request_relay",
            "_start_host_control_relay",
            "_stop_host_control_relay",
        ):
            source = function_source(name)
            self.assertIn("QThread.currentThread() != app.thread()", source)
            self.assertIn("_run_on_ui_thread", source)
        close_start = MAIN_SOURCE.index('    def closeEvent(self, event):\n        """Close main window -> quit whole app.')
        close_end = MAIN_SOURCE.index("\n    def load_data", close_start)
        close = MAIN_SOURCE[close_start:close_end]
        self.assertIn("self._shutdown_network_transports()", close)
        shutdown = function_source("_shutdown_network_transports")
        self.assertIn("_network_transports_shutdown", shutdown)
        self.assertIn("self._stop_request_relay()", shutdown)
        self.assertIn("self._stop_host_control_relay()", shutdown)
        self.assertIn("timer.stop()", shutdown)
        self.assertIn("_QT_APP_SHUTTING_DOWN = True", shutdown)
        self.assertIn("sock.blockSignals(True)", MAIN_SOURCE)
        self.assertIn("sock.close()", MAIN_SOURCE)
        self.assertIn("sock.abort()", MAIN_SOURCE)
        self.assertIn("sock.setParent(None)", MAIN_SOURCE)
        self.assertIn("_retired_sockets", MAIN_SOURCE)
        self.assertIn("QApplication.processEvents()", shutdown)
        self.assertIn("QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)", shutdown)
        request_stop = function_source("_stop_request_relay")
        host_stop = function_source("_stop_host_control_relay")
        self.assertIn("app_closing = bool(getattr(self, \"_app_closing\", False))", request_stop)
        self.assertIn("worker.stop(delete_later=not app_closing)", request_stop)
        self.assertIn("worker.setParent(None)", request_stop)
        self.assertIn("_shutdown_relay_workers", request_stop)
        self.assertIn("app_closing = bool(getattr(self, \"_app_closing\", False))", host_stop)
        self.assertIn("worker.stop(delete_later=not app_closing)", host_stop)
        self.assertIn("worker.setParent(None)", host_stop)
        self.assertIn("_shutdown_relay_workers", host_stop)
        about_to_quit = function_source("_on_app_about_to_quit")
        self.assertIn("self._app_closing = True", about_to_quit)
        self.assertIn("_QT_APP_SHUTTING_DOWN = True", about_to_quit)
        self.assertIn("self._shutdown_network_transports()", about_to_quit)
        main_block = MAIN_SOURCE[MAIN_SOURCE.index('if __name__ == "__main__":'):]
        self.assertIn("app.aboutToQuit.connect(window._on_app_about_to_quit)", main_block)
        run_on_ui = function_source("_run_on_ui_thread")
        self.assertIn('if bool(getattr(self, "_app_closing", False)):', run_on_ui)
        self.assertIn("owner_thread = self.thread()", run_on_ui)

    def test_qt_websocket_shutdown_warnings_are_filtered_only_during_shutdown(self):
        self.assertIn("qInstallMessageHandler", MAIN_SOURCE)
        start = MAIN_SOURCE.index("def _singws_qt_message_handler")
        end = MAIN_SOURCE.index("\n\ntry:\n    _QT_PREVIOUS_MESSAGE_HANDLER", start)
        handler = MAIN_SOURCE[start:end]
        self.assertIn("_QT_APP_SHUTTING_DOWN", handler)
        self.assertIn("_QT_SOCKET_TEARDOWN_WARNING_PARTS", handler)
        self.assertIn("previous(mode, context, message)", handler)
        self.assertIn("sys.__stderr__.write", handler)
        self.assertIn("QNativeSocketEngine", MAIN_SOURCE)
        self.assertIn("QSslSocket", MAIN_SOURCE)
        self.assertIn("QWebSocketDataProcessor", MAIN_SOURCE)

    def test_waitlist_needs_review_and_daw_preview_live_guards(self):
        wait_refresh = function_source("_refresh_waiting_for_add_view")
        self.assertIn('"needs_review": QColor("#86efac")', wait_refresh)
        self.assertIn('if status_kind == "needs_review":', wait_refresh)
        self.assertIn("self._waiting_for_add_pulse_value()", wait_refresh)
        pulse_tick = function_source("_tick_waiting_for_add_pulse")
        self.assertIn("self._needs_review_count() > 0", pulse_tick)
        self.assertIn("needs_review_pulse", pulse_tick)

        daw_stopped = function_source("_mark_daw_preview_playback_stopped")
        self.assertIn("playback_stopped_immediate", daw_stopped)
        self.assertIn("playback_stopped_retry", daw_stopped)
        self.assertNotIn("_post_daw_singer_screen_snapshot(None, active=False", daw_stopped)

        direct_message = function_source("_net_send_direct_message")
        self.assertIn("system_notice: bool = False", direct_message)
        self.assertIn('"system_notice": "1" if system_notice else "0"', direct_message)
        waiting_refresh = function_source("_schedule_waiting_for_add_view_refresh")
        self.assertIn("owner_thread = self.thread()", waiting_refresh)
        self.assertIn("self._run_on_ui_thread(lambda: self._schedule_waiting_for_add_view_refresh", waiting_refresh)
        history_refresh = function_source("_schedule_singer_history_refresh")
        self.assertIn("owner_thread = self.thread()", history_refresh)
        self.assertIn("self._run_on_ui_thread(lambda: self._schedule_singer_history_refresh", history_refresh)
        self.assertIn('if bool(getattr(self, "_app_closing", False)):', function_source("_dispatch_ui_call"))

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

    def test_singer_history_brand_choices_cache_key_method_is_not_shadowed(self):
        self.assertIn("def _history_brand_choices_cache_key", MAIN_SOURCE)
        self.assertIn("_history_brand_choices_cached_key", MAIN_SOURCE)
        self.assertNotIn("self._history_brand_choices_cache_key =", MAIN_SOURCE)

    def test_daw_preview_network_failures_have_backoff(self):
        self.assertIn("def _daw_preview_server_backoff_active", MAIN_SOURCE)
        self.assertIn("def _record_daw_preview_server_failure", MAIN_SOURCE)
        self.assertIn("def _record_daw_preview_server_success", MAIN_SOURCE)
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("_daw_preview_server_backoff_active", scheduler)
        self.assertLess(
            scheduler.index("_daw_preview_server_backoff_active"),
            scheduler.index("requests.get("),
        )
        self.assertIn("_record_daw_preview_server_failure", scheduler)
        self.assertIn("_record_daw_preview_server_success", scheduler)
        uploader = function_source("_post_daw_singer_screen_snapshot")
        self.assertIn("_record_daw_preview_server_failure", uploader)
        self.assertIn("_record_daw_preview_server_success", uploader)

    def test_daw_preview_timer_is_adaptive(self):
        init_source = MAIN_SOURCE[MAIN_SOURCE.index("self._daw_snapshot_timer = QTimer(self)"):]
        init_source = init_source[:init_source.index("self.rotation_view = None")]
        self.assertIn('self._retune_daw_snapshot_timer("startup")', init_source)
        self.assertNotIn("self._daw_snapshot_timer.start(1000)", init_source)
        target = function_source("_daw_snapshot_timer_target_ms")
        self.assertIn("return 1000", target)
        self.assertIn("return 5000", target)
        self.assertIn("_daw_snapshot_viewer_recent()", target)
        tuner = function_source("_retune_daw_snapshot_timer")
        self.assertIn("timer.stop()", tuner)
        self.assertIn("timer.start(target_ms)", tuner)
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("_daw_snapshot_viewer_recent_until", scheduler)
        self.assertIn('_retune_daw_snapshot_timer("viewer_check")', scheduler)
        settings = MAIN_SOURCE[MAIN_SOURCE.index("def on_daw_preview_toggled"):]
        settings = settings[:settings.index("daw_preview_cb.toggled.connect")]
        self.assertIn('_retune_daw_snapshot_timer("settings_toggle")', settings)

    def test_idle_show_screen_background_changes_fade(self):
        self.assertIn("_background_fade_timer = QTimer(self)", MAIN_SOURCE)
        setter = function_source("set_background_image")
        self.assertIn("_background_previous_pixmap", setter)
        self.assertIn("_background_fade_started = time.monotonic()", setter)
        self.assertIn("_background_fade_timer.start(33)", setter)
        forced = function_source("fade_background_from_black")
        self.assertIn("_background_forced_fade_last", forced)
        self.assertIn("_background_previous_pixmap = QPixmap()", forced)
        apply_idle = function_source("_apply_idle_background")
        self.assertIn("fade_background_from_black()", apply_idle)
        self.assertLess(apply_idle.index("self.video_window.set_background_image(path)"), apply_idle.index("fade_background_from_black()"))
        paint = function_source("paintEvent")
        self.assertIn("fade_alpha = self._background_fade_alpha()", paint)
        self.assertIn("painter.setOpacity(fade_alpha)", paint)
        self.assertIn("_draw_background_pixmap(painter, previous)", paint)

    def test_volume_analysis_dialog_is_resurfaced_frontmost(self):
        analyze = function_source("analyze_library")
        self.assertIn("WindowStaysOnTopHint", analyze)
        self.assertIn("Measuring loudness", analyze)
        self.assertIn("self._bring_analyze_dialog_to_front(_d)", analyze)
        self.assertIn("self._bring_analyze_dialog_to_front(dlg)", analyze)
        bring = function_source("_bring_analyze_dialog_to_front")
        self.assertIn("dlg.show()", bring)
        self.assertIn("dlg.raise_()", bring)
        self.assertIn("dlg.activateWindow()", bring)
        self.assertIn("QTimer.singleShot", bring)

    def test_cdg_near_black_cleanup_clamps_only_black_pixels(self):
        image = QImage(3, 1, QImage.Format.Format_ARGB32)
        image.setPixelColor(0, 0, QColor(4, 6, 8))
        image.setPixelColor(1, 0, QColor(9, 2, 30))
        image.setPixelColor(2, 0, QColor(220, 220, 220))
        cleaned = self.singws._clean_cdg_near_black(image, 10)
        self.assertEqual(cleaned.pixelColor(0, 0).getRgb()[:3], (0, 0, 0))
        self.assertEqual(cleaned.pixelColor(1, 0).getRgb()[:3], (9, 2, 30))
        self.assertEqual(cleaned.pixelColor(2, 0).getRgb()[:3], (220, 220, 220))
        transparent = self.singws._clean_cdg_near_black(image, 10, transparent_black=True)
        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(transparent.pixelColor(1, 0).alpha(), 255)
        self.assertEqual(transparent.pixelColor(2, 0).alpha(), 255)

    def test_cdg_near_black_cleanup_uses_indexed_palette_fast_path(self):
        image = QImage(3, 1, QImage.Format.Format_Indexed8)
        image.setColorTable([
            QColor(4, 6, 8).rgba(),
            QColor(9, 2, 30).rgba(),
            QColor(220, 220, 220).rgba(),
        ])
        image.setPixel(0, 0, 0)
        image.setPixel(1, 0, 1)
        image.setPixel(2, 0, 2)

        cleaned = self.singws._clean_cdg_near_black(image, 10)
        self.assertEqual(cleaned.format(), QImage.Format.Format_Indexed8)
        self.assertEqual(cleaned.pixelColor(0, 0).getRgb()[:3], (0, 0, 0))
        self.assertEqual(cleaned.pixelColor(1, 0).getRgb()[:3], (9, 2, 30))
        self.assertEqual(cleaned.pixelColor(2, 0).getRgb()[:3], (220, 220, 220))

        transparent = self.singws._clean_cdg_near_black(image, 10, transparent_black=True)
        self.assertEqual(transparent.format(), QImage.Format.Format_Indexed8)
        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(transparent.pixelColor(1, 0).alpha(), 255)
        self.assertEqual(transparent.pixelColor(2, 0).alpha(), 255)

    def test_audio_chain_logs_cover_bgm_and_karaoke(self):
        bg_chain = function_source("_bg_dsp_chain_label")
        self.assertIn("master_audio", bg_chain)
        karaoke_log = function_source("_log_karaoke_audio_chain")
        self.assertIn("[KARAOKE-AUDIO]", karaoke_log)
        self.assertIn("normalize", karaoke_log)
        self.assertIn("master_audio", karaoke_log)

    def test_main_thread_stall_watchdog_is_diagnostic_gated(self):
        self.assertIn("def _install_main_thread_watchdog", MAIN_SOURCE)
        start = MAIN_SOURCE.index("def _install_main_thread_watchdog")
        end = MAIN_SOURCE.index("# Settings defaults", start)
        watchdog = MAIN_SOURCE[start:end]
        self.assertIn("SINGWS_NO_WATCHDOG", watchdog)
        self.assertIn("performance_debug_enabled", watchdog)
        self.assertIn("[STALL]", watchdog)
        self.assertIn("sys._current_frames()", watchdog)
        self.assertIn("singws-main-thread-watchdog", watchdog)
        self.assertIn("_install_main_thread_watchdog(self)", MAIN_SOURCE)
        self.assertIn("self._mt_watch_stop = True", MAIN_SOURCE)

    def test_runtime_diagnostics_are_opt_in_by_default(self):
        self.assertIn('"performance_debug_enabled": False', MAIN_SOURCE)
        self.assertIn('"performance_debug_default_migrated": False', MAIN_SOURCE)
        self.assertIn('self.settings["performance_debug_enabled"] = False', MAIN_SOURCE)
        startup = MAIN_SOURCE[
            MAIN_SOURCE.index("# DIAGNOSTIC: Freeze detector"):
            MAIN_SOURCE.index("self.setup_selection_behavior()", MAIN_SOURCE.index("# DIAGNOSTIC: Freeze detector"))
        ]
        self.assertIn('self.settings.get("performance_debug_enabled", False)', startup)
        self.assertIn("SingWSLogger.log_gstreamer_runtime_diagnostics()", startup)
        self.assertIn("SingWSLogger.log_library_stats", startup)

    def test_show_screen_does_not_paint_idle_background_during_active_karaoke(self):
        start = MAIN_SOURCE.index("class VideoAreaWidget")
        end = MAIN_SOURCE.index("class MusicDatabaseWidget", start)
        video_area = MAIN_SOURCE[start:end]
        self.assertIn("def _is_live_karaoke_active", video_area)
        self.assertIn('getattr(owner, "karaoke_playing", False)', video_area)
        self.assertIn('getattr(vw, "idle", True)', video_area)
        paint = function_source("paintEvent")
        self.assertIn("not self._is_live_karaoke_active()", paint)
        recreate = function_source("recreate_video_surface")
        self.assertIn("new_area.karaoke_frame = QImage(old.karaoke_frame)", recreate)
        self.assertIn("new_area._ensure_karaoke_scaled_pixmap()", recreate)

    def test_removed_slow_computer_settings_are_migrated(self):
        source = MAIN_SOURCE
        self.assertNotIn('"performance_mode": False', source)
        self.assertNotIn('"safe_mode": False', source)
        self.assertIn('for obsolete_key in ("performance_mode", "safe_mode")', source)

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
