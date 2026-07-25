import ast
import pathlib
import re
import unittest
import importlib.util

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


MAIN_SOURCE = pathlib.Path("0.2.18.1.py").read_text(encoding="utf-8")
TRANSPORT_SOURCE = pathlib.Path("python_karaoke_transport.py").read_text(encoding="utf-8")


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
        self.assertIn("now - last < 60.0", diag)
        self.assertIn("_active_worker_log_last_signature", diag)

    def test_memory_telemetry_is_periodic_and_growth_sensitive(self):
        start = function_source("_start_memory_telemetry")
        self.assertIn("timer.start(5 * 60 * 1000)", start)
        self.assertIn("timer.timeout.connect(self._log_memory_telemetry)", start)
        telemetry = function_source("_log_memory_telemetry")
        self.assertIn("psutil.Process(os.getpid()).memory_info().rss", telemetry)
        self.assertIn("growth_mb >= 128.0", telemetry)
        self.assertIn("30 * 60", telemetry)
        self.assertIn("[MEMORY]", telemetry)
        self.assertIn("queue_songs=", telemetry)

    def test_high_frequency_diagnostics_are_rate_limited(self):
        helper_start = MAIN_SOURCE.index("def _diag_rate_limited")
        helper_end = MAIN_SOURCE.index("\ndef ", helper_start + 4)
        helper = MAIN_SOURCE[helper_start:helper_end]
        self.assertIn("_DIAG_RATE_LIMIT_LAST", helper)
        self.assertIn("now - last < max(0.0, float(interval_sec))", helper)
        relay = function_source("_relay_fetch_finished")
        self.assertIn("_diag_rate_limited(", relay)
        recovery = function_source("_network_recovery_tick")
        self.assertIn("_diag_rate_limited(", recovery)

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
        self.assertIn("_singer_history_refresh_deferred", history_refresh)
        self.assertIn("timer.start(1000)", history_refresh)
        history_build = function_source("_refresh_singer_history_view")
        self.assertIn('if bool(getattr(self, "karaoke_playing", False)):', history_build)
        history_apply = function_source("_on_singer_history_directory_built")
        self.assertIn("worker_finished_during_playback", history_apply)
        history_details = function_source("_update_singer_history_details")
        self.assertIn("details_requested_during_playback", history_details)
        history_songs_apply = function_source("_on_singer_history_songs_built")
        self.assertIn("songs_worker_finished_during_playback", history_songs_apply)
        self.assertIn('if bool(getattr(self, "_app_closing", False)):', function_source("_dispatch_ui_call"))

    def test_karaoke_start_does_not_predecode_entire_track(self):
        source = pathlib.Path("python_karaoke_transport.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        delayed_start = ""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_finish_delayed_start":
                delayed_start = ast.get_source_segment(source, node) or ""
                break
        self.assertTrue(delayed_start)
        self.assertNotIn("_start_full_decode", delayed_start)

    def test_mp4_reader_does_not_reemit_duplicate_frames(self):
        source = pathlib.Path("python_karaoke_transport.py").read_text(encoding="utf-8")
        reader_start = source.index("class FfmpegVideoReader")
        reader_end = source.index("class _PcmFeeder")
        reader = source[reader_start:reader_end]
        self.assertIn("return self.latest_image if selected is not None else None", reader)
        self.assertIn("self.timer.setTimerType(Qt.TimerType.PreciseTimer)", source)

    def test_cdg_file_io_is_warmed_off_the_gui_thread(self):
        source = pathlib.Path("python_karaoke_transport.py").read_text(encoding="utf-8")
        decoder_start = source.index("class CdgDecoder")
        decoder_end = source.index("class FfmpegVideoReader")
        decoder = source[decoder_start:decoder_end]
        init_start = decoder.index("    def __init__")
        load_start = decoder.index("    def _load_packets")
        init = decoder[init_start:load_start]
        self.assertNotIn("open(", init)
        self.assertIn("singws-cdg-late-preload", init)

        schedule = function_source("_schedule_next_up_prescan")
        self.assertIn("_preload_next_up_cdg", schedule)
        preload = function_source("_preload_next_up_cdg")
        self.assertIn("threading.Thread", preload)
        self.assertIn("singws-next-cdg-preload", preload)
        legacy_add = function_source("add_song_to_singer")
        self.assertIn("_enqueue_cdg_pair_async", legacy_add)
        self.assertNotIn("os.path.exists(mp3_path)", legacy_add)
        pair_check = function_source("_enqueue_cdg_pair_async")
        self.assertIn("threading.Thread", pair_check)
        self.assertIn("preload_cdg_packets(cdg_path)", pair_check)

    def test_library_path_lookup_uses_an_index(self):
        lookup = function_source("_get_track_obj")
        self.assertIn("_find_track_by_path_ci", lookup)
        indexed = function_source("_find_track_by_path_ci")
        self.assertIn("_track_path_lookup", indexed)
        self.assertNotIn("for t in self.tracks", indexed)

    def test_uncached_loudness_lookup_avoids_file_stat(self):
        for name in ("loudness_info_cached", "loudness_gain_db_cached"):
            start = MAIN_SOURCE.index(f"def {name}")
            end = MAIN_SOURCE.index("\ndef ", start + 4)
            source = MAIN_SOURCE[start:end]
            self.assertLess(source.index("_loudness_cache.get"), source.index("_loudness_file_sig"))

    def test_noop_server_sync_avoids_history_rebuild_and_repeat_request_diagnostics(self):
        history_merge = function_source("_merge_remote_singer_history")
        self.assertIn("if local_song != remote_song:", history_merge)
        self.assertIn("if changed:", history_merge)
        self.assertIn('self._schedule_singer_history_refresh(reason="remote_merge")', history_merge)
        request_diag = function_source("_log_remote_request_diag")
        self.assertIn("_remote_request_diag_signatures", request_diag)
        self.assertIn("cache.get(request_id) == diag_signature", request_diag)
        self.assertIn("len(cache) > 2048", request_diag)

    def test_karaoke_level_meter_tracks_output_not_decoder_lookahead(self):
        transport = pathlib.Path("python_karaoke_transport.py").read_text(encoding="utf-8")
        feeder_start = transport.index("class _PcmFeeder")
        feeder_end = transport.index("class PythonKaraokeTransport")
        feeder = transport[feeder_start:feeder_end]
        self.assertIn("t._accept_level(bytes(out))", feeder)
        accept_start = transport.index("    def _accept_level")
        accept_end = transport.index("    def _mark_decoder_done", accept_start)
        accept = transport[accept_start:accept_end]
        self.assertIn("if worker is not None:", accept)
        self.assertIn("return", accept)

    def test_ffmpeg_cdg_timing_restores_legacy_baseline(self):
        self.assertIn("FFMPEG_CDG_BASE_OFFSET_MS = 600", MAIN_SOURCE)
        effective = function_source("_effective_cdg_timing_offset_ms")
        self.assertIn("FFMPEG_CDG_BASE_OFFSET_MS + fine_ms", effective)
        start = function_source("_start_python_karaoke_transport")
        self.assertIn("off = self._effective_cdg_timing_offset_ms()", start)
        self.assertIn("ffmpeg_cdg_timing_migrated", MAIN_SOURCE)

    def test_deferred_remote_adds_save_off_thread_during_playback(self):
        source = function_source("_save_deferred_remote_adds")
        self.assertIn("karaoke_playing", source)
        self.assertIn("_start_deferred_remote_save_worker", source)
        worker = function_source("_start_deferred_remote_save_worker")
        self.assertIn("threading.Thread", worker)
        self.assertIn("singws-save-deferred-remote", worker)

    def test_scan_folder_starts_background_worker_before_inline_fallback(self):
        marker = '    def _legacy_scan_folder(self):\n        """Scan chooser: Quick Update (incremental) or Full Scan."""'
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

    def test_rotation_level_meter_runs_only_during_active_playback(self):
        app = QApplication.instance() or QApplication([])
        meter = self.singws.BarLevelMeter()
        self.assertFalse(meter._timer.isActive())

        meter.set_active(True)
        self.assertTrue(meter._timer.isActive())

        meter.set_active(False)
        self.assertFalse(meter._timer.isActive())
        self.assertEqual(meter._heights, [0.0] * meter._n_bars)
        meter.deleteLater()
        app.processEvents()

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
        self.assertIn("Waitlist: no pending host action", update)
        self.assertIn("need host action", update)
        self.assertIn("rgba(22, 163, 74", update)
        self.assertIn("_waiting_for_add_nav_css_cache_key", update)

    def test_header_status_shows_request_and_waitlist_state(self):
        init_source = MAIN_SOURCE[MAIN_SOURCE.index("accepting_text = \"Requests Open\""):]
        init_source = init_source[:init_source.index("self.header_location_label")]
        self.assertIn("Requests Open", init_source)
        self.assertIn("Waitlist On", init_source)
        refresh = function_source("_refresh_header_status")
        self.assertIn("Requests Open", refresh)
        self.assertIn("Waitlist On", refresh)
        self.assertIn("Waitlist mode:", refresh)
        self.assertIn('self.settings.get("location_name")', refresh)
        self.assertIn('or self.settings.get("venue_name")', refresh)
        self.assertIn('or self.settings.get("tenant")\n                    or self.settings.get("user")', refresh)

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

    def test_daw_preview_frame_callbacks_honor_viewer_and_server_backoff(self):
        frame_sender = function_source("_maybe_send_daw_snapshot_from_frame")
        self.assertIn("_daw_snapshot_viewer_recent()", frame_sender)
        self.assertIn("_daw_preview_server_backoff_active", frame_sender)
        uploader = function_source("_post_daw_singer_screen_snapshot")
        self.assertIn("_daw_preview_server_backoff_active", uploader)

    def test_daw_preview_does_not_capture_without_recent_viewer(self):
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("should_capture = enabled and viewer_recent", scheduler)
        self.assertNotIn("viewer_recent or playing or bool(force)", scheduler)

    def test_daw_preview_timer_is_adaptive(self):
        init_source = MAIN_SOURCE[MAIN_SOURCE.index("self._daw_snapshot_timer = QTimer(self)"):]
        init_source = init_source[:init_source.index("self.rotation_view = None")]
        self.assertIn('self._retune_daw_snapshot_timer("startup")', init_source)
        self.assertNotIn("self._daw_snapshot_timer.start(1000)", init_source)
        target = function_source("_daw_snapshot_timer_target_ms")
        self.assertIn("return 1000", target)
        self.assertIn("return 5000", target)
        self.assertLess(
            target.index("_daw_preview_server_backoff_until"),
            target.index("_daw_snapshot_viewer_recent()"),
        )
        self.assertIn("_daw_snapshot_viewer_recent()", target)
        self.assertNotIn("karaoke_playing", target)
        tuner = function_source("_retune_daw_snapshot_timer")
        self.assertIn("timer.stop()", tuner)
        self.assertIn("timer.start(target_ms)", tuner)
        scheduler = function_source("_schedule_daw_singer_screen_snapshot")
        self.assertIn("_daw_snapshot_viewer_recent_until", scheduler)
        self.assertIn('_retune_daw_snapshot_timer("viewer_check")', scheduler)
        settings = MAIN_SOURCE[MAIN_SOURCE.index("def on_daw_preview_toggled"):]
        settings = settings[:settings.index("daw_preview_cb.toggled.connect")]
        self.assertIn('_retune_daw_snapshot_timer("settings_toggle")', settings)

    def test_slow_sync_perf_logs_are_rate_limited(self):
        source = self.singws._perf_log_if_slow.__code__.co_consts
        joined = " ".join(str(item) for item in source)
        self.assertIn("server", joined)
        self.assertIn("sync", joined)
        self.assertIn("json", joined)
        self.assertIn("15.0", joined)

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

    def test_operator_preview_mirrors_audience_rendering_state(self):
        self.assertIn("def _cdg_side_fill_color(image)", MAIN_SOURCE)
        frame_handler = function_source("_on_python_karaoke_frame")
        self.assertIn('cdg_display_mode == "sidefill"', frame_handler)
        self.assertEqual(frame_handler.count("side_fill=side_fill"), 2)
        preview_start = MAIN_SOURCE.index("class PreviewWindow(QWidget):")
        preview_end = MAIN_SOURCE.index("class PerformanceWaveformWidget", preview_start)
        preview = MAIN_SOURCE[preview_start:preview_end]
        self.assertIn("self.video_area = VideoAreaWidget(self)", preview)
        self.assertIn("def recreate_video_surface", preview)
        idle = function_source("_show_idle_background_after_karaoke")
        self.assertIn("self.preview_window.force_black = False", idle)
        qr = function_source("_refresh_show_screen_qr")
        self.assertIn("preview_area", qr)
        self.assertIn("preview_area.set_request_qr(None)", qr)
        self.assertIn("for candidate in (area,)", qr)
        overlay = function_source("_show_next_up_transition_overlay")
        self.assertIn("preview_area.show_next_up_overlay", overlay)

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

    def test_cdg_near_black_cleanup_scales_to_full_resolution_rgba(self):
        # The RGBA path must handle a full-resolution frame (e.g. an MP4 frame,
        # up to 4K) without the old per-pixel Python loop that froze the GUI
        # thread. This exercises the vectorized clamp on a non-trivial frame and
        # pins its correctness at scale.
        w, h = 640, 480
        image = QImage(w, h, QImage.Format.Format_ARGB32)
        image.fill(QColor(3, 5, 7))                 # near-black background
        for x in range(0, w, 40):
            image.setPixelColor(x, 5, QColor(255, 190, 0))   # bright "lyrics"
        image.setPixelColor(1, 1, QColor(0, 0, 0))  # pure black stays pure black

        cleaned = self.singws._clean_cdg_near_black(image, 10)
        # near-black background clamped to pure black
        self.assertEqual(cleaned.pixelColor(300, 300).getRgb()[:3], (0, 0, 0))
        # bright text preserved
        self.assertEqual(cleaned.pixelColor(0, 5).getRgb()[:3], (255, 190, 0))

        transparent = self.singws._clean_cdg_near_black(image, 10, transparent_black=True)
        self.assertEqual(transparent.pixelColor(300, 300).alpha(), 0)     # bg see-through
        self.assertEqual(transparent.pixelColor(0, 5).alpha(), 255)       # text opaque

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
        self.assertIn("duplicate recreate skipped", recreate)
        self.assertIn("(now - last_completed) < 1.25", recreate)
        self.assertIn("old_overlay.setParent(new_area)", recreate)
        self.assertIn("new_area.set_show_vfx_overlay(old_overlay)", recreate)

    def test_fallback_transition_timeout_is_owned_by_video_area(self):
        start = MAIN_SOURCE.index("class VideoAreaWidget")
        end = MAIN_SOURCE.index("class MusicDatabaseWidget", start)
        video_area = MAIN_SOURCE[start:end]
        self.assertIn("self._fallback_transition_timer = QTimer(self)", video_area)
        self.assertIn(
            "self._fallback_transition_timer.timeout.connect(self._clear_fallback_transition)",
            video_area,
        )
        fallback = function_source("_show_fallback_transition")
        self.assertIn("self._fallback_transition_timer.start(", fallback)
        self.assertNotIn("QTimer.singleShot", fallback)

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
        self.assertNotIn("silent_prefire", timer_tick)
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("self._karaoke_early_silence_trim_enabled()", trim)
        self.assertNotIn("self._karaoke_bgm_crossfade_enabled()", trim)
        self.assertNotIn("BG overlap fade-in started at trim", trim)
        self.assertIn("and (not end_silence_triggered)", end_handler)
        fade = function_source("_start_bg_with_fade")
        self.assertIn('resume_reason == "karaoke_end_overlap" and self._karaoke_bgm_crossfade_enabled()', fade)

    def test_trailing_silence_uses_shorter_safe_handoff_threshold(self):
        self.assertIn('"end_silence_trim_threshold_sec": 2.5', MAIN_SOURCE)
        self.assertIn("self._end_silence_min_s = 2.5", MAIN_SOURCE)
        self.assertIn("old_threshold in (5.0, 6.0)", MAIN_SOURCE)
        self.assertIn('self.settings["end_silence_trim_threshold_sec"] = 2.5', MAIN_SOURCE)
        trim = function_source("_maybe_trim_end_silence")
        self.assertIn("cdg_lyrics_finished", trim)
        self.assertIn("near_end", trim)

    def test_high_frame_rate_hevc_uses_bounded_software_decode_profile(self):
        self.assertIn('self.codec_name in {"hevc", "h265"}', TRANSPORT_SOURCE)
        self.assertIn("self.source_fps > 45.0", TRANSPORT_SOURCE)
        self.assertIn("self.fps = min(self.fps, 24.0)", TRANSPORT_SOURCE)
        self.assertIn("adaptive_height = 540", TRANSPORT_SOURCE)
        self.assertIn('command.extend(["-threads", "2"])', TRANSPORT_SOURCE)

    def test_videotoolbox_probe_rejects_silent_software_fallback(self):
        self.assertIn('"hwaccel initialisation returned error"', TRANSPORT_SOURCE)
        self.assertIn('"failed setup for format videotoolbox"', TRANSPORT_SOURCE)
        self.assertIn(
            "result.returncode == 0 and not any(marker in errors",
            TRANSPORT_SOURCE,
        )

    def test_singer_history_edits_use_debounced_save_path(self):
        source = function_source("_commit_singer_history_change")
        self.assertIn("_schedule_save_data", source)
        self.assertNotIn("self.save_data()", source)

    def test_hidden_singer_history_does_not_rebuild_models(self):
        schedule = function_source("_schedule_singer_history_refresh")
        self.assertIn("stack.currentWidget() is history_page", schedule)
        self.assertIn("_singer_history_refresh_hidden_deferred = True", schedule)
        self.assertIn("timer.stop()", schedule)
        refresh = function_source("_refresh_singer_history_view")
        self.assertIn(
            "self.left_workspace_stack.currentWidget() is not self.singer_history_page",
            refresh,
        )
        switch = function_source("_set_left_workspace_view")
        self.assertLess(
            switch.index("self.left_workspace_stack.setCurrentWidget(self.singer_history_page)"),
            switch.index('self._schedule_singer_history_refresh(reason="tab_switch")'),
        )

    def test_remote_reconcile_collapses_duplicate_singer_rows(self):
        reconcile = function_source("_reconcile_remote_requests")
        self.assertIn(
            '_merge_duplicate_rotation_singers(reason="remote_reconcile_start")',
            reconcile,
        )
        self.assertIn(
            '_merge_duplicate_rotation_singers(reason="remote_reconcile_finish")',
            reconcile,
        )

    def test_queue_insert_reuses_same_display_name_across_server_sessions(self):
        match = function_source("_queue_singer_match_index")
        self.assertIn("display-name identity collision reused existing row", match)
        self.assertNotIn("existing_server_id != singer_id:\n                    continue", match)
        add = function_source("_add_song_to_queue")
        self.assertIn("preserved existing row identity while attaching request", add)

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
