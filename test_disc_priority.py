import importlib.util
import os
import unittest


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_disc_priority", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiscPriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def matches(self, *disc_ids):
        return [
            {
                "display": f"{disc_id} Version",
                "disc_id": disc_id,
                "path": f"/tmp/{disc_id}.mp4",
            }
            for disc_id in disc_ids
        ]

    def test_singer_sc_promotes_soundchoice_when_available(self):
        host = self.singws.normalize_disc_priority("Karaoke Version, ZOOM, CC, Karafun, Party Tyme, SBI, SoundChoice, SunFly")
        info = self.singws.effective_disc_priority(host, singer_brand_raw="SC")

        chosen = self.singws.pick_by_disc_priority(self.matches("KV123", "SC456", "ZOOM789"), info["effective"])

        self.assertEqual(info["effective"][:3], ["SC", "KV", "ZM"])
        self.assertEqual(chosen["disc_id"], "SC456")

    def test_singer_sc_unavailable_falls_back_to_host_order(self):
        host = self.singws.normalize_disc_priority("KV, ZOOM, CC, SC")
        info = self.singws.effective_disc_priority(host, singer_brand_raw="SoundChoice")

        chosen = self.singws.pick_by_disc_priority(self.matches("ZOOM789", "KV123"), info["effective"])

        self.assertEqual(info["effective"], ["SC", "KV", "ZM", "CC"])
        self.assertEqual(chosen["disc_id"], "KV123")

    def test_singer_kv_alias_selects_karaoke_version(self):
        host = self.singws.normalize_disc_priority("ZOOM, CC, SC")
        info = self.singws.effective_disc_priority(host, singer_brand_raw="Karaoke Version")

        chosen = self.singws.pick_by_disc_priority(self.matches("ZOOM789", "KV123"), info["effective"])

        self.assertEqual(info["singer_normalized"], "KV")
        self.assertEqual(chosen["disc_id"], "KV123")

    def test_zoom_aliases_share_one_priority_slot(self):
        host = self.singws.normalize_disc_priority("ZM, SC")
        info = self.singws.effective_disc_priority(host)

        chosen = self.singws.pick_by_disc_priority(self.matches("SC456", "ZDL9988"), info["effective"])

        self.assertEqual(info["effective"], ["ZM", "SC"])
        self.assertEqual(chosen["disc_id"], "ZDL9988")

    def test_chartbuster_aliases_match_cb_priority_not_cc(self):
        host = self.singws.normalize_disc_priority("CC, KV, CB")
        info = self.singws.effective_disc_priority(host)

        chosen = self.singws.pick_by_disc_priority(self.matches("KV123", "CB6021"), info["effective"])

        self.assertEqual(info["effective"], ["CC", "KV", "CB"])
        self.assertEqual(chosen["disc_id"], "KV123")

        cb_first = self.singws.pick_by_disc_priority(self.matches("KV123", "Chartbuster6021"), ["CB", "KV"])
        self.assertEqual(cb_first["disc_id"], "Chartbuster6021")

    def test_cc_is_chris_call_only_and_does_not_swallow_other_brands(self):
        # CC = Chris Call Karaoke: matches literal "CC" only, never "Custom",
        # "Chartbuster", or CB-coded discs.
        self.assertEqual(self.singws.canonical_disc_brand("CC", allow_unknown=False), "CC")
        self.assertEqual(self.singws.canonical_disc_brand("Custom", allow_unknown=False), "")
        self.assertEqual(self.singws.canonical_disc_brand("Chartbuster", allow_unknown=False), "CB")

        # A song requested for CC must not be satisfied by a Chartbuster disc.
        chosen = self.singws.pick_by_disc_priority(self.matches("CB6021", "CC777"), ["CC"])
        self.assertEqual(chosen["disc_id"], "CC777")

    def test_pt_sf_sbi_karafun_and_me_stay_separate(self):
        priority = self.singws.normalize_disc_priority("PT, SF, SBI, KARAFUN, ME")

        self.assertEqual(priority, ["PT", "SF", "SBI", "KARAFUN", "ME"])
        self.assertEqual(self.singws.canonical_disc_brand("Party Tyme", allow_unknown=False), "PT")
        self.assertEqual(self.singws.canonical_disc_brand("SunFly", allow_unknown=False), "SF")
        self.assertEqual(self.singws.canonical_disc_brand("SBI Karaoke", allow_unknown=False), "SBI")
        self.assertEqual(self.singws.canonical_disc_brand("KaraFun", allow_unknown=False), "KARAFUN")
        self.assertEqual(self.singws.canonical_disc_brand("Mr Entertainer", allow_unknown=False), "ME")

        chosen = self.singws.pick_by_disc_priority(
            self.matches("SF123", "SBI456", "KARAFUN789", "ME111", "PT222"),
            ["SBI"],
        )
        self.assertEqual(chosen["disc_id"], "SBI456")

    def test_filename_segment_can_match_when_disc_id_missing(self):
        host = self.singws.normalize_disc_priority("KV, ZM")
        matches = [
            {"display": "Artist - Song - ZDL123", "path": "/tmp/Artist - Song - ZDL123.mp4"},
            {"display": "Artist - Song - Karaoke-Version-555", "path": "/tmp/Artist - Song - Karaoke-Version-555.mp4"},
        ]

        chosen = self.singws.pick_by_disc_priority(matches, host)

        self.assertEqual(chosen["path"], "/tmp/Artist - Song - Karaoke-Version-555.mp4")

    def test_blank_preference_uses_host_order_unchanged(self):
        host = self.singws.normalize_disc_priority("KV, ZOOM, SC")
        info = self.singws.effective_disc_priority(host, singer_brand_raw="")

        self.assertEqual(info["effective"], host)
        self.assertEqual(self.singws.pick_by_disc_priority(self.matches("SC456", "KV123"), info["effective"])["disc_id"], "KV123")

    def test_unknown_singer_brand_is_ignored(self):
        host = self.singws.normalize_disc_priority("KV, ZOOM, SC")
        info = self.singws.effective_disc_priority(host, singer_brand_raw="Mystery Brand")

        self.assertTrue(info["unknown_ignored"])
        self.assertEqual(info["effective"], host)
        self.assertEqual(self.singws.pick_by_disc_priority(self.matches("SC456", "KV123"), info["effective"])["disc_id"], "KV123")

    def test_effective_priority_does_not_mutate_host_priority(self):
        host = self.singws.normalize_disc_priority("KV, ZOOM, SC")
        original = list(host)

        info = self.singws.effective_disc_priority(host, singer_brand_raw="SC")

        self.assertEqual(host, original)
        self.assertEqual(info["effective"], ["SC", "KV", "ZM"])

    def test_scan_report_groups_known_aliases_and_reports_unknown_prefixes(self):
        report = self.singws.build_disc_brand_scan_report([
            {"disc_id": "KV123"},
            {"disc_id": "Karaoke Version 456"},
            {"disc_id": "ZDL789"},
            {"display": "Artist - Song - MYST999", "path": "/tmp/Artist - Song - MYST999.mp4"},
        ])

        self.assertEqual(report["canonical_counts"]["KV"], 2)
        self.assertEqual(report["canonical_counts"]["ZM"], 1)
        self.assertEqual(report["unknown_prefixes"][0]["prefix"], "MYST")


if __name__ == "__main__":
    unittest.main()
