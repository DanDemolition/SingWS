import os
import tempfile
import unittest
from pathlib import Path

import phrase_markers as pm


class BarsMathTests(unittest.TestCase):
    def test_bars_to_seconds_120bpm(self):
        # 120 BPM -> 0.5 s/beat. 4 bars=16 beats=8s, 8 bars=16s, 16 bars=32s.
        self.assertAlmostEqual(pm.bars_to_seconds(4, 120), 8.0)
        self.assertAlmostEqual(pm.bars_to_seconds(8, 120), 16.0)
        self.assertAlmostEqual(pm.bars_to_seconds(16, 120), 32.0)

    def test_bars_to_seconds_other_bpm(self):
        self.assertAlmostEqual(pm.bars_to_seconds(8, 128), 32 * 60.0 / 128)

    def test_bars_to_seconds_invalid(self):
        self.assertIsNone(pm.bars_to_seconds(8, 0))
        self.assertIsNone(pm.bars_to_seconds(8, -5))
        self.assertIsNone(pm.bars_to_seconds(0, 120))
        self.assertIsNone(pm.bars_to_seconds(8, None))

    def test_labels_and_kinds(self):
        self.assertEqual(pm.bar_label(4), "4 Bar")
        self.assertEqual(pm.bar_label(16), "16 Bar")
        self.assertEqual(pm.bar_kind(8), "bar8")


class ClampResolveTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(pm.clamp_start_seconds(-3, 200), 0.0)
        self.assertEqual(pm.clamp_start_seconds(0, 200), 0.0)
        self.assertEqual(pm.clamp_start_seconds(10, 200), 10.0)
        # never start within 2s of the end
        self.assertEqual(pm.clamp_start_seconds(199, 200), 198.0)
        self.assertEqual(pm.clamp_start_seconds(None, 200), 0.0)

    def test_resolve_precedence(self):
        # explicit per-instance override wins, including explicit 0 (Beginning)
        self.assertEqual(pm.resolve_start_seconds(16.0, 8.0, 300), 16.0)
        self.assertEqual(pm.resolve_start_seconds(0.0, 8.0, 300), 0.0)
        # no override -> reuse saved default
        self.assertEqual(pm.resolve_start_seconds(None, 8.0, 300), 8.0)
        # neither -> 0
        self.assertEqual(pm.resolve_start_seconds(None, None, 300), 0.0)
        # clamp applies through resolve
        self.assertEqual(pm.resolve_start_seconds(None, 999.0, 100), 98.0)


class SongKeyTests(unittest.TestCase):
    def test_song_key_from_metadata(self):
        self.assertEqual(pm.song_key_for(artist="Queen", title="We Will Rock You", discid="SC1234"),
                         "queen|we will rock you|sc1234")

    def test_song_key_fallback_to_filename(self):
        self.assertEqual(pm.song_key_for(path="/music/Artist - Title.mp3"), "artist - title")


class MarkersDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Path(self.tmp.name)

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_bar_marker_idempotent_upsert(self):
        path = "/songs/a.mp3"
        id1 = pm.upsert_marker(path, kind="bar8", seconds=16.0, bars=8, bpm=120,
                               label="8 Bar", source="bpm", dbfile=self.db)
        id2 = pm.upsert_marker(path, kind="bar8", seconds=15.5, bars=8, bpm=124,
                               label="8 Bar", source="bpm", dbfile=self.db)
        self.assertEqual(id1, id2)  # updated in place, not duplicated
        rows = pm.list_markers(path, dbfile=self.db)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["seconds"], 15.5)
        self.assertAlmostEqual(rows[0]["bpm"], 124)

    def test_multiple_custom_markers_and_edit_delete(self):
        path = "/songs/b.mp3"
        c1 = pm.upsert_marker(path, kind="custom", seconds=12.3, label="Custom", source="manual", dbfile=self.db)
        c2 = pm.upsert_marker(path, kind="custom", seconds=45.6, label="Custom", source="manual", dbfile=self.db)
        self.assertNotEqual(c1, c2)
        self.assertEqual(len(pm.list_markers(path, dbfile=self.db)), 2)
        # edit one by id
        pm.upsert_marker(path, kind="custom", seconds=50.0, label="Custom", source="manual",
                         marker_id=c2, dbfile=self.db)
        rows = {r["id"]: r for r in pm.list_markers(path, dbfile=self.db)}
        self.assertAlmostEqual(rows[c2]["seconds"], 50.0)
        # delete one
        pm.delete_marker(c1, dbfile=self.db)
        self.assertEqual(len(pm.list_markers(path, dbfile=self.db)), 1)

    def test_default_selection(self):
        path = "/songs/c.mp3"
        pm.upsert_marker(path, kind="bar4", seconds=8.0, bars=4, bpm=120, label="4 Bar",
                         source="bpm", dbfile=self.db)
        eight = pm.upsert_marker(path, kind="bar8", seconds=16.0, bars=8, bpm=120, label="8 Bar",
                                 source="bpm", make_default=True, dbfile=self.db)
        d = pm.default_marker(path, dbfile=self.db)
        self.assertEqual(d["id"], eight)
        self.assertAlmostEqual(d["seconds"], 16.0)
        # switching default
        cust = pm.upsert_marker(path, kind="custom", seconds=30.0, label="Custom", source="manual",
                                make_default=True, dbfile=self.db)
        self.assertEqual(pm.default_marker(path, dbfile=self.db)["id"], cust)

    def test_song_key_fallback_lookup(self):
        key = pm.song_key_for(artist="A", title="B")
        pm.upsert_marker("/songs/d.mp3", kind="custom", seconds=5.0, label="Custom",
                         source="manual", song_key=key, dbfile=self.db)
        # lookup by a different/empty path but same key
        rows = pm.list_markers("", song_key=key, dbfile=self.db)
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(pm.default_marker("/nonexistent.mp3", song_key=key, dbfile=self.db))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Path(self.tmp.name)

    def tearDown(self):
        for p in (self.tmp.name,):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_uuid_assigned_on_insert(self):
        mid = pm.upsert_marker("/s/a.mp3", kind="custom", seconds=5.0, label="Custom", dbfile=self.db)
        rows = pm.export_all(dbfile=self.db)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["uuid"])

    def test_soft_delete_hides_but_keeps_tombstone(self):
        mid = pm.upsert_marker("/s/b.mp3", kind="custom", seconds=9.0, label="Custom", dbfile=self.db)
        pm.delete_marker(mid, dbfile=self.db)
        self.assertEqual(len(pm.list_markers("/s/b.mp3", dbfile=self.db)), 0)
        allrows = pm.export_all(include_deleted=True, dbfile=self.db)
        self.assertEqual(len(allrows), 1)
        self.assertIsNotNone(allrows[0]["deleted_at"])

    def test_apply_remote_last_write_wins(self):
        uid = "fixed-uuid-1"
        # incoming newer than nothing -> inserts
        n = pm.apply_remote([{ "uuid": uid, "song_path": "/s/c.mp3", "song_key": "c",
                               "kind": "custom", "seconds": 10.0, "label": "Custom",
                               "source": "manual", "updated_at": 100, "created_at": 100 }], dbfile=self.db)
        self.assertEqual(n, 1)
        # older update is ignored
        pm.apply_remote([{ "uuid": uid, "seconds": 99.0, "updated_at": 50 }], dbfile=self.db)
        rows = pm.list_markers("/s/c.mp3", dbfile=self.db)
        self.assertAlmostEqual(rows[0]["seconds"], 10.0)
        # newer update wins
        pm.apply_remote([{ "uuid": uid, "song_path": "/s/c.mp3", "song_key": "c", "kind": "custom",
                           "seconds": 22.0, "updated_at": 200 }], dbfile=self.db)
        rows = pm.list_markers("/s/c.mp3", dbfile=self.db)
        self.assertAlmostEqual(rows[0]["seconds"], 22.0)

    def test_apply_remote_tombstone_propagates(self):
        uid = "fixed-uuid-2"
        pm.apply_remote([{ "uuid": uid, "song_path": "/s/d.mp3", "song_key": "d", "kind": "custom",
                           "seconds": 10.0, "updated_at": 100 }], dbfile=self.db)
        self.assertEqual(len(pm.list_markers("/s/d.mp3", dbfile=self.db)), 1)
        pm.apply_remote([{ "uuid": uid, "deleted_at": 150, "updated_at": 150 }], dbfile=self.db)
        self.assertEqual(len(pm.list_markers("/s/d.mp3", dbfile=self.db)), 0)

    def test_changed_since(self):
        pm.apply_remote([{ "uuid": "u-old", "song_path": "/s/e.mp3", "kind": "custom",
                           "seconds": 1.0, "updated_at": 100 }], dbfile=self.db)
        pm.apply_remote([{ "uuid": "u-new", "song_path": "/s/e.mp3", "kind": "custom",
                           "seconds": 2.0, "updated_at": 300 }], dbfile=self.db)
        delta = pm.changed_since(200, dbfile=self.db)
        self.assertEqual({r["uuid"] for r in delta}, {"u-new"})

    def test_json_round_trip(self):
        pm.upsert_marker("/s/f.mp3", kind="custom", seconds=12.0, label="Custom", dbfile=self.db)
        pm.upsert_marker("/s/f.mp3", kind="bar8", seconds=16.0, bars=8, bpm=120, label="8 Bar",
                         source="bpm", dbfile=self.db)
        backup = self.db.with_suffix(".json")
        try:
            count = pm.export_to_json(str(backup), dbfile=self.db)
            self.assertEqual(count, 2)
            # import into a fresh db
            tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp2.close()
            db2 = Path(tmp2.name)
            applied = pm.import_from_json(str(backup), dbfile=db2)
            self.assertEqual(applied, 2)
            self.assertEqual(len(pm.list_markers("/s/f.mp3", dbfile=db2)), 2)
            os.unlink(tmp2.name)
        finally:
            try:
                os.unlink(str(backup))
            except OSError:
                pass


class ResolvePhraseStartIntegrationTests(unittest.TestCase):
    """Exercises the KaraokeApp._resolve_phrase_start glue against the real module.
    Requires SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 (set by the test runner)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("singws_main_phrase", "0.2.18.1.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _app(self):
        return self.mod.KaraokeApp.__new__(self.mod.KaraokeApp)

    def test_entry_override_wins(self):
        app = self._app()
        # explicit per-instance value beats any saved default
        with _patched_default(self.mod, {"seconds": 8.0}):
            self.assertAlmostEqual(app._resolve_phrase_start("/x.mp3", {"phrase_start_seconds": 20.0}, 300), 20.0)
            # explicit 0.0 (Beginning) also wins over the default
            self.assertEqual(app._resolve_phrase_start("/x.mp3", {"phrase_start_seconds": 0.0}, 300), 0.0)

    def test_default_marker_reused_and_clamped(self):
        app = self._app()
        with _patched_default(self.mod, {"seconds": 12.0}):
            self.assertAlmostEqual(app._resolve_phrase_start("/x.mp3", {}, 300), 12.0)
        with _patched_default(self.mod, {"seconds": 999.0}):
            self.assertAlmostEqual(app._resolve_phrase_start("/x.mp3", {}, 100), 98.0)  # clamp to dur-2
        with _patched_default(self.mod, None):
            self.assertEqual(app._resolve_phrase_start("/x.mp3", {}, 300), 0.0)

    def test_sync_config_and_noop_without_network(self):
        app = self._app()
        # missing base_url/api_key -> config empty, push is a guarded no-op (no raise)
        app.settings = {"user": "demo"}
        base, tenant, key = app._phrase_sync_config()
        self.assertEqual(tenant, "demo")
        self.assertEqual(key, "")
        app._sync_push_phrase_markers()   # must not raise
        app._pull_phrase_markers()        # must not raise

    def test_sync_methods_exist(self):
        app = self._app()
        for name in ("export_phrase_markers", "import_phrase_markers",
                     "_sync_push_phrase_markers", "_pull_phrase_markers",
                     "_phrase_preview_at", "open_phrase_start_dialog"):
            self.assertTrue(callable(getattr(app, name, None)), name)

    def test_never_raises(self):
        app = self._app()
        # a marker lookup that blows up must degrade to 0.0, not crash playback
        def boom(*a, **k):
            raise RuntimeError("db gone")
        orig = self.mod.phrase_markers.default_marker
        self.mod.phrase_markers.default_marker = boom
        try:
            self.assertEqual(app._resolve_phrase_start("/x.mp3", {}, 300), 0.0)
        finally:
            self.mod.phrase_markers.default_marker = orig


class _patched_default:
    """Context manager: stub the module's phrase_markers.default_marker."""
    def __init__(self, mod, value):
        self.mod = mod
        self.value = value

    def __enter__(self):
        self._orig = self.mod.phrase_markers.default_marker
        self.mod.phrase_markers.default_marker = lambda *a, **k: self.value
        return self

    def __exit__(self, *exc):
        self.mod.phrase_markers.default_marker = self._orig
        return False


if __name__ == "__main__":
    unittest.main()
