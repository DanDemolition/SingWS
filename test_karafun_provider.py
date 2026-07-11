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


if __name__ == "__main__":
    unittest.main()
