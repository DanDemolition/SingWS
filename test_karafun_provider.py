import ast
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import song_index


class KaraFunProviderTests(unittest.TestCase):

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





    def test_server_catalog_request_uses_manual_karafun_track_shape(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("def _build_karafun_streaming_track", source)
        prepare = source[source.index("def _prepare_remote_request_add_payload"):]
        prepare = prepare[:prepare.index("def _apply_resolved_remote_add")]
        self.assertIn("is_karafun = self._remote_request_is_karafun(req)", prepare)
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
        self.assertIn('QCheckBox("Include KaraFun catalog results in search")', source)
        self.assertIn('_save_karafun_choice("karafun_include_online_search", checked)', source)
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
