import base64, io, os, struct, tempfile, unittest, wave
import announcement_box as ab


def wav(samples, rate=32000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return b.getvalue()


class FakeEngine:
    def __init__(self):
        self.loaded_paths, self.existed_at_load, self.played, self.instances, self._closed = [], [], 0, 0, False
        self.released = 0
    def pad_allocate_slot(self): return 3
    def pad_load(self, slot, path, volume):
        self.loaded_paths.append(path); self.existed_at_load.append(os.path.exists(path))
    def pad_play(self, slot, start): self.played += 1; self.instances = 1; return True
    def pad_instances(self, slot): return self.instances
    def pad_stop(self, slot): self.instances = 0
    def pad_release_slot(self, slot): self.released += 1


def send(box, clip, clip_id="a", size=700, rate=32000):
    parts = [clip[i:i + size] for i in range(0, len(clip), size)]
    r = [box.handle("announce.begin", {"id": clip_id, "rate": rate, "chunks": len(parts)})]
    for i, p in enumerate(parts):
        r.append(box.handle("announce.chunk", {"id": clip_id, "index": i, "data": base64.b64encode(p).decode()}))
    r.append(box.handle("announce.end", {"id": clip_id}))
    return r


class AnnouncementBoxTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: [os.unlink(os.path.join(self.dir, n)) for n in os.listdir(self.dir)] or os.rmdir(self.dir))
        self.now = [0.0]
        self.box = ab.AnnouncementBox(lambda: self.engine, clock=lambda: self.now[0], temp_dir=self.dir)
        self.clip = wav([int(8000 * ((i % 40) - 20) / 20) for i in range(32000)])

    def test_upload_play_leaves_nothing_on_disk(self):
        self.assertTrue(all(ok for ok, _ in send(self.box, self.clip)))
        self.assertEqual(self.box.armed_id, "a")
        self.assertEqual(self.box.handle("announce.play", {"id": "a"}), (True, "playing"))
        self.assertEqual(os.listdir(self.dir), [])
        self.assertEqual(self.engine.existed_at_load, [True])
        self.assertEqual(self.engine.played, 1)
        self.assertEqual(self.box.armed_id, "")
        self.engine.instances = 0
        import time; time.sleep(0.07)      # the channel counts a hit as playing for 50 ms
        self.now[0] += 1.0
        self.box.tick()
        self.assertEqual(self.engine.released, 1)

    def test_no_second_play_and_discard(self):
        send(self.box, self.clip)
        self.box.handle("announce.play", {"id": "a"})
        self.assertEqual(self.box.handle("announce.play", {"id": "a"}), (False, "no_clip_armed"))
        send(self.box, self.clip, "b")
        self.assertEqual(self.box.handle("announce.discard", {}), (True, "discarded"))
        self.assertFalse(self.box.holds_audio())

    def test_wrong_id_and_order_refused(self):
        self.box.handle("announce.begin", {"id": "a", "rate": 32000, "chunks": 2})
        self.assertEqual(self.box.handle("announce.chunk", {"id": "x", "index": 0, "data": ""})[1], "no_clip_in_progress")
        self.box.handle("announce.begin", {"id": "a", "rate": 32000, "chunks": 2})
        self.assertEqual(self.box.handle("announce.chunk", {"id": "a", "index": 1, "data": ""})[1], "chunk_out_of_order")
        self.box.handle("announce.begin", {"id": "a", "rate": 32000, "chunks": 2})
        self.box.handle("announce.chunk", {"id": "a", "index": 0, "data": "AAAA"})
        self.assertEqual(self.box.handle("announce.end", {"id": "a"})[1], "clip_incomplete")

    def test_bad_audio_refused(self):
        stereo = io.BytesIO()
        with wave.open(stereo, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(32000); w.writeframes(b"\0\0" * 200)
        self.assertEqual(send(self.box, stereo.getvalue())[-1][1], "wav_must_be_mono_16bit")
        self.assertEqual(send(self.box, b"not a wav")[-1][1], "bad_wav")
        self.assertEqual(send(self.box, wav([32767, -32768] * 100))[-1][1], "clip_would_distort")
        self.assertEqual(send(self.box, wav([100] * (32000 * 31)), size=60000)[-1][1], "clip_too_long")
        self.assertEqual(send(self.box, wav([]))[-1][1], "empty_clip")
        self.assertFalse(self.box.holds_audio())

    def test_unplayed_clip_expires(self):
        send(self.box, self.clip)
        self.now[0] += ab.HOLD_SECONDS + 1
        self.assertEqual(self.box.handle("announce.play", {"id": "a"})[1], "no_clip_armed")

    def test_no_engine_keeps_nothing_on_disk(self):
        box = ab.AnnouncementBox(lambda: None, temp_dir=self.dir)
        send(box, self.clip)
        self.assertEqual(box.handle("announce.play", {"id": "a"})[1], "audio_engine_not_running")
        self.assertEqual(os.listdir(self.dir), [])

    def test_sweep(self):
        open(os.path.join(self.dir, "announce_x.wav"), "wb").close()
        self.assertEqual(ab.sweep_stale_files(self.dir), 1)
        self.assertEqual(os.listdir(self.dir), [])


class PhoneSideTests(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SingWS-Server")

    def test_dsp_js_checks(self):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        r = subprocess.run([node, os.path.join(self.ROOT, "tools", "test_announce_dsp.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_recorder_never_uploads_to_the_server(self):
        src = open(os.path.join(self.ROOT, "v2", "assets", "announce.js")).read()
        for forbidden in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "indexedDB", "FormData"):
            self.assertNotIn(forbidden, src)
        self.assertIn("DAW_BRIDGE.command", src)


if __name__ == "__main__":
    unittest.main()
