import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import subprocess
import unittest
from pathlib import Path

import requests

from tools import network_inventory as ni
from tools import wire_parity as wp

CLI = os.environ.get("SINGWS_WIRE_CLI") or wp.CLI_DEFAULT
ROOT = Path(__file__).parent
needs_cli = unittest.skipUnless(Path(CLI).exists(), "needs singws-wire-cli (cargo build --release -p singws-wire)")


def rust(case):
    return wp.rust_batch(CLI, [case])[0]


@needs_cli
class DifferentialTests(unittest.TestCase):
    def test_random_calls_prepare_identically_to_requests(self):
        cases = wp.make_cases(8000, 4242)
        stats, bad = wp.compare(cases, wp.rust_batch(CLI, cases))
        self.assertEqual(bad[:2], [], f"{len(bad)} differences")
        self.assertEqual(stats["compared"], 8000)

    def test_the_apps_own_call_shapes(self):
        # shapes copied from the real call sites (secrets replaced); the headers go through untouched so they are not part of the wire body
        tenant, rid = "dan", 1792783785132
        cases = [
            {"method": "POST", "url": "https://beta.wskar.com/api/v1/complete_remote_request.php",
             "data": {"user": tenant, "request_id": rid, "state": "removed", "status": "removed", "singer_name": "Test", "artist": "NOFX",
                      "title": "Linoleum", "removed_at": 1792783948, "removed_by": "host", "removal_reason": "host_remove", "local_revision": 3}},
            {"method": "POST", "url": "https://beta.wskar.com/api/v1/delete_remote_request.php", "data": {"user": tenant, "request_id": rid}},
            {"method": "POST", "url": "https://beta.wskar.com/api/v1/set_accepting.php", "data": {"user": tenant, "accepting": 1}},
            {"method": "GET", "url": "https://beta.wskar.com/api/v1/get_requests.php", "params": {"user": tenant, "since": 0, "limit": 200}},
            {"method": "POST", "url": "https://beta.wskar.com/api/v1/singer_history_sync.php", "json": {"user": tenant, "singers": [{"name": "Zoë", "songs": 3, "last": 1792783948.5}]}},
            {"method": "POST", "url": "https://beta.wskar.com/api/v1/upsert_host_request.php?tenant=dan", "data": {"request_key": "host:a:local:b", "title": "Don't Stop", "singer": "Ünal & Co"}},
            {"method": "POST", "url": "https://beta.wskar.com/api/v1/clear_remote_queue.php", "data": {}},
        ]
        stats, bad = wp.compare(cases, wp.rust_batch(CLI, cases))
        self.assertEqual((bad, stats["compared"]), ([], len(cases)))

    def test_float_formatting_follows_python_repr_in_form_values(self):
        values = [0.1, 1.0, 1e16, 1e-5, 123456789.123456789, 5e-324, 1.7976931348623157e308, -0.0, 2.5e-7, 0.30000000000000004, 900719925474099.25]
        cases = [{"method": "POST", "url": "https://x.test/f.php", "data": {"v": v}} for v in values]
        _, bad = wp.compare(cases, wp.rust_batch(CLI, cases))
        self.assertEqual(bad, [])


@needs_cli
class DeclinedCasesTests(unittest.TestCase):
    """Inputs Rust refuses; each is justified by showing requests does something Rust does not claim to reproduce."""

    def test_urls_that_requests_requotes_are_declined(self):
        for url in ("https://x.test/a b.php", "https://x.test/é.php", "https://x.test/a%20b.php", "https://x.test/a\\b.php"):
            case = {"method": "GET", "url": url}
            self.assertFalse(rust(case)["ok"], url)
            self.assertTrue(wp.oracle(case)["ok"], "requests accepts it, so declining is a real limitation, not an accident")
        self.assertNotEqual(wp.oracle({"method": "GET", "url": "https://x.test/a b.php"})["url"], "https://x.test/a b.php",
                            "requests rewrites the URL, which is why Rust must not pass it through")

    def test_nested_objects_in_form_values_are_declined(self):
        case = {"method": "POST", "url": "https://x.test/f.php", "data": {"k": {"a": 1, "b": 2}}}
        self.assertFalse(rust(case)["ok"])
        self.assertIn("k=a&k=b", wp.oracle(case)["body"], "requests iterates the object's KEYS, a trap worth refusing")

    def test_unsupported_schemes_and_hosts_are_declined(self):
        for url in ("ftp://x.test/", "x.test/a.php", "https:///a.php", "file:///etc/passwd"):
            self.assertFalse(rust({"method": "GET", "url": url})["ok"], url)


@needs_cli
class SafetyTests(unittest.TestCase):
    def test_the_cli_survives_garbage_and_never_needs_a_network(self):
        for text in ("", "not json", "[]", "null", "[1, 2]", '{"method": 5}'):
            out = subprocess.run([CLI], input=text, capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, text)
            json.loads(out.stdout)

    def test_the_crate_has_no_networking_code_or_dependency(self):
        cargo = (ROOT / "rust/crates/singws-wire/Cargo.toml").read_text()
        deps = cargo[cargo.index("[dependencies]"):]
        for name in ("reqwest", "hyper", "ureq", "tokio", "rustls", "native-tls", "curl", "isahc", "attohttpc", "surf", "tungstenite"):
            self.assertNotIn(name, deps)
        src = ""
        for p in (ROOT / "rust/crates/singws-wire/src").rglob("*.rs"):
            src += p.read_text()
        for needle in ("std::net", "TcpStream", "UdpSocket", "TcpListener", "ToSocketAddrs"):
            self.assertNotIn(needle, src)


class WiringTests(unittest.TestCase):
    def test_the_wire_crate_never_networks_and_the_app_never_uses_its_cli(self):
        app = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
        self.assertNotIn("wire_cli", app)
        self.assertNotIn("singws-wire-cli", app)
        # the pure request builder IS exported from the library for the wire shadow (singws_wire_json); the crate it comes from cannot network
        ffi = (ROOT / "rust/crates/singws-dsp-ffi/Cargo.toml").read_text()
        self.assertIn("singws-wire", ffi)
        for name in ("reqwest", "hyper", "ureq", "tokio", "rustls", "native-tls", "tungstenite"):
            self.assertNotIn(name, ffi)

    def test_requests_version_matches_the_one_the_rules_were_derived_from(self):
        major, minor = (int(x) for x in requests.__version__.split(".")[:2])
        self.assertGreaterEqual((major, minor), (2, 32), "re-run tools/wire_parity.py after upgrading requests")


class InventoryTests(unittest.TestCase):
    """Pins the facts about the app's network surface, so a new call site or a weaker call is noticed in review."""

    @classmethod
    def setUpClass(cls):
        cls.calls = ni.inventory([ROOT / "0.2.18.1.py"])
        cls.facts = ni.facts(cls.calls)

    def test_every_call_has_a_timeout_and_none_disables_tls_verification(self):
        self.assertEqual([(c["line"], c["function"]) for c in self.facts["no_timeout"]], [])
        self.assertEqual([(c["line"], c["function"]) for c in self.facts["verify_disabled"]], [])

    def test_the_known_api_surface(self):
        self.assertEqual(self.facts["total"], 56, "the number of requests.get/post calls changed: review the new call and update docs/network_inventory.md")
        self.assertEqual((self.facts["get"], self.facts["post"]), (17, 39))
        self.assertEqual(len(self.facts["endpoints"]), 31)
        for must in ("delete_remote_request.php", "complete_remote_request.php", "set_remote_request_order.php", "upsert_host_request.php",
                     "restore_remote_request.php", "clear_remote_queue.php", "get_requests_v2.php"):
            self.assertIn(must, self.facts["endpoints"])

    def test_only_get_and_post_are_used(self):
        self.assertEqual({c["method"] for c in self.calls}, {"GET", "POST"})

    def test_the_committed_inventory_document_is_current(self):
        doc = (ROOT / "docs/network_inventory.md").read_text(encoding="utf-8")
        self.assertEqual(doc.strip(), ni.render(self.calls, markdown=True).strip(),
                         "docs/network_inventory.md is stale: python3 tools/network_inventory.py --markdown > docs/network_inventory.md")


if __name__ == "__main__":
    unittest.main()
