#!/usr/bin/env python3
"""Mutation test for the network slices: rust/crates/singws-netslice (lib.rs), net_slice_shadow.py and the hook in 0.2.18.1.py's probe_network_sync_status.
A mutant is killed when the crate's unit tests, tools/net_slice_parity.py (Rust files) or test_net_slice_shadow.py (Python files) fail. Equivalent mutants
carry a note. Run it ALONE (it rewrites source files and rebuilds per mutant).

  python3 tools/mutate_net_slice.py [--only 3,7]     CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-netslice
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "rust/crates/singws-netslice"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-netslice"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, QT_QPA_PLATFORM="offscreen", SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS="1")
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
PY = os.environ.get("SINGWS_PYTHON", "/Users/daniel/Documents/SingWS/.venv/bin/python")
LIB, SHADOW, APP = CRATE / "src/lib.rs", ROOT / "net_slice_shadow.py", ROOT / "0.2.18.1.py"

M = [
    # --- text rules
    (LIB, "matches!(c, '\\t'..='\\r' | '\\x1c'..='\\x1f' | ' ')", "matches!(c, '\\t'..='\\r' | ' ')", None),
    (LIB, "matches!(c, '\\t'..='\\r' | '\\x1c'..='\\x1f' | ' ')", "matches!(c, '\\x1c'..='\\x1f' | ' ')", None),
    (LIB, "let quote = if s.contains('\\'') && !s.contains('\"') { '\"' } else { '\\'' };", "let quote = '\\'';", None),
    (LIB, "let quote = if s.contains('\\'') && !s.contains('\"') { '\"' } else { '\\'' };", "let quote = if s.contains('\\'') { '\"' } else { '\\'' };", None),
    (LIB, "'\\\\' => out.push_str(\"\\\\\\\\\"),", "'\\\\' => out.push('\\\\'),", None),
    (LIB, "'\\r' => out.push_str(\"\\\\r\"),", "'\\r' => out.push('\\r'),", None),
    (LIB, "'\\t' => out.push_str(\"\\\\t\"),", "'\\t' => out.push('\\t'),", None),
    (LIB, "c if (' '..='~').contains(&c) => out.push(c),", "c if (' '..='}').contains(&c) => out.push(c),", None),
    (LIB, "s.chars().take(300).collect()", "s.chars().take(301).collect()", None),
    (LIB, "k.is_ascii() && all_ascii(v)", "all_ascii(v)", "no object key is ever non-ASCII in a request the shadow builds"),
    (LIB, "Value::String(s) => s.is_ascii(),", "Value::String(s) => true,", None),
    # --- config / normalisation
    (LIB, ".trim_matches(py_space).trim_end_matches('/').to_string();", ".trim_matches(py_space).to_string();", None),
    (LIB, ".trim_matches(py_space).trim_end_matches('/').to_string();", ".trim_end_matches('/').to_string();", None),
    (LIB, "!base_in.is_empty() && !base_in.contains(\"://\")", "!base_in.contains(\"://\")", None),
    (LIB, "let user = text(req, \"tenant\")?.trim_matches(py_space).to_string();", "let user = text(req, \"tenant\")?.to_string();", None),
    (LIB, "let key = text(req, \"api_key\")?.trim_matches(py_space).to_string();", "let key = text(req, \"api_key\")?.to_string();", None),
    (LIB, "let f = if f == 0.0 { 6.0 } else { f };", "let f = if f == 0.0 { 5.0 } else { f };", None),
    (LIB, "f.max(1.0)", "f.max(2.0)", None),
    (LIB, "base.is_empty() || !(base.starts_with(\"http://\") || base.starts_with(\"https://\"))", "!(base.starts_with(\"http://\") || base.starts_with(\"https://\"))", "an empty base cannot start with http"),
    (LIB, "base.is_empty() || !(base.starts_with(\"http://\") || base.starts_with(\"https://\"))", "base.is_empty() || !base.starts_with(\"https://\")", None),
    (LIB, "if user.is_empty() {\n        p.record(\"config\"", "if user.len() < 2 {\n        p.record(\"config\"", None),
    (LIB, "headers.insert(\"Accept\".into(), json!(\"application/json\"));", "headers.insert(\"Accept\".into(), json!(\"*/*\"));", None),
    (LIB, "json!(\"SingWS/network-sync\")", "json!(\"SingWS/network\")", None),
    (LIB, "if !key.is_empty() {\n        headers.insert", "if key.is_empty() {\n        headers.insert", None),
    # --- plan
    (LIB, "json!({\"user\": user, \"sync\": \"1\"})", "json!({\"user\": user, \"sync\": \"0\"})", None),
    (LIB, "json!({\"_\": now})", "json!({\"_\": now + 1})", None),
    (LIB, "let t5 = timeout.min(5.0);", "let t5 = timeout.min(6.0);", None),
    (LIB, "let t4 = timeout.min(4.0);", "let t4 = timeout.min(5.0);", None),
    (LIB, "json!({\"user\": user, \"action\": \"poll\"})", "json!({\"user\": user, \"action\": \"pull\"})", None),
    (LIB, "json!({\"user\": user, \"_probe\": \"1\"})", "json!({\"user\": user})", None),
    (LIB, "for name in [\"singer_history\", \"rotation\"] {", "for name in [\"rotation\", \"singer_history\"] {", None),
    (LIB, "ep(\"/get_requests.php\")", "ep(\"/get_request.php\")", None),
    (LIB, "format!(\"{base}/tenants/{user}/accepting.txt\")", "format!(\"{base}/tenants/{user}/accepting\")", None),
    (LIB, "ep(\"/api/v1/host_commands.php\")", "ep(\"/api/v1/host_command.php\")", None),
    (LIB, "ep(\"/post_rotation.php\")", "ep(\"/post_rotations.php\")", None),
    (LIB, "ep(\"/api/v1/set_remote_request_modifiers.php\")", "ep(\"/api/v1/set_remote_request_modifier.php\")", None),
    (LIB, "ep(\"/api/v1/clear_remote_queue.php\")", "ep(\"/api/v1/clear_queue.php\")", None),
    (LIB, "ep(\"/api/v1/set_remote_request_order.php\")", "ep(\"/api/v1/set_request_order.php\")", None),
    # --- decisions
    (LIB, "Some(200) => {\n                if json_ok {", "Some(200) => {\n                if !json_ok {", None),
    (LIB, "Some(401) | Some(403) => p.record(\"request_intake\"", "Some(401) => p.record(\"request_intake\"", None),
    (LIB, "\"request intake rejected credentials\"", "\"request intake failed\"", None),
    (LIB, "&format!(\"invalid JSON: {json_error}\")", "&format!(\"invalid JSON {json_error}\")", None),
    (LIB, "format!(\"timeout after {timeout:.1}s: {e}\")", "format!(\"timeout after {timeout:.0}s: {e}\")", None),
    (LIB, "format!(\"connection error: {e}\")", "format!(\"ConnectionError: {e}\")", None),
    (LIB, "if txt == \"0\" || txt == \"1\" {", "if txt == \"1\" {", None),
    (LIB, "let txt = body.trim_matches(py_space);", "let txt = body.as_str();", None),
    (LIB, "json!(txt == \"1\")", "json!(txt == \"0\")", None),
    (LIB, "&format!(\"accepting={txt}\")", "&format!(\"accepting={}\", txt.len())", None),
    (LIB, "format!(\"timeout after {t5:.1}s: {e}\")", "format!(\"timeout after {timeout:.1}s: {e}\")", None),
    (LIB, 'p.record("host_controls", false, Value::Null, &format!("timeout after {t4:.1}s: {e}")', 'p.record("host_controls", false, Value::Null, &format!("timeout after {t5:.1}s: {e}")', None),
    (LIB, "let ok = int_status(&status) == Some(200);\n                p.record(\"host_controls\"", "let ok = int_status(&status).is_some();\n                p.record(\"host_controls\"", None),
    (LIB, "if ok { \"\" } else { &body }, false);\n            }\n            Outcome::Timeout(e) => p.record(\"host_controls\"", "\"\", false);\n            }\n            Outcome::Timeout(e) => p.record(\"host_controls\"", None),
    (LIB, "p.record(\"host_controls\", true, Value::Null, \"API key not configured\", \"\", true);", "p.record(\"host_controls\", false, Value::Null, \"API key not configured\", \"\", true);", None),
    (LIB, "p.record(\"host_controls\", true, Value::Null, \"API key not configured\", \"\", true);", "p.record(\"host_controls\", true, Value::Null, \"API key not configured\", \"\", false);", None),
    (LIB, "matches!(int_status(&status), Some(200) | Some(400) | Some(405))", "matches!(int_status(&status), Some(200) | Some(405))", None),
    (LIB, "matches!(int_status(&status), Some(200) | Some(400) | Some(405))", "matches!(int_status(&status), Some(400) | Some(405))", None),
    (LIB, "\"endpoint reachable\" } else { \"endpoint unavailable\" }", "\"endpoint unavailable\" } else { \"endpoint reachable\" }", None),
    (LIB, "result.insert(\"partial\".into(), json!(request_ok && !failures.is_empty()));", "result.insert(\"partial\".into(), json!(!failures.is_empty()));", None),
    (LIB, "n.as_str() != \"request_intake\" && !c[\"ok\"]", "!c[\"ok\"]", "partial needs request_ok, and then the intake check is ok, so it never is a failure"),
    (LIB, "&& !c[\"skipped\"].as_bool().unwrap_or(false))", ")", "a skipped check is always recorded ok=true, so the ok test already excludes it"),
    (LIB, "\"Connected; partial sync issue: {}\", failures.join(\", \")", "\"Connected; partial sync issue: {}\", failures.join(\",\")", None),
    (LIB, "\"Connected / Synced\".to_string()", "\"Connected\".to_string()", None),
    (LIB, ".unwrap_or_else(|| \"Sync failed\".to_string())", ".unwrap_or_else(|| \"Failed\".to_string())", "no corpus case has a failed intake with no error text"),
    (LIB, "if skipped || ok {\n            return;", "if skipped {\n            return;", None),
    (LIB, "if skipped || ok {\n            return;", "if ok {\n            return;", "a skipped check is always recorded ok=true"),
    (LIB, "Value::Null => \"None\".to_string(),", "Value::Null => \"null\".to_string(),", None),
    (LIB, ".trim_matches(py_space).to_string();\n        if !body.is_empty() {", ".to_string();\n        if !body.is_empty() {", None),
    (LIB, "detail.push_str(&format!(\" body={}\", py_repr(body)));", "detail.push_str(&format!(\" body={body}\"));", None),
    (LIB, "for k in [\"ok\", \"connected\", \"partial\", \"message\", \"base_url\", \"tenant\", \"accepting\", \"checks\", \"errors\", \"endpoints\"] {", "for k in [\"ok\", \"connected\", \"partial\", \"message\", \"base_url\", \"tenant\", \"accepting\", \"checks\", \"endpoints\", \"errors\"] {", None),
    (LIB, "let now = now.ok_or(\"no 'now' for the accepting probe\")?;", "let now = now.unwrap_or(0);", None),
    # --- python shadow
    (SHADOW, "        if list(a) != list(b):", "        if False:", None),
    (SHADOW, "if type(a) is not type(b):", "if False:", None),
    (SHADOW, 'return f"{path}.<len>", len(a), len(b)', "return None", None),
    (SHADOW, "    if \"unsupported\" in rust:\n        return None", "    if False:\n        return None", None),
    (SHADOW, "    if not isinstance(rust, dict):\n        return \"rust answer is not an object\"", "    if False:\n        return \"rust answer is not an object\"", None),
    (SHADOW, "if not isinstance(out, list) or len(out) != len(requests):", "if not isinstance(out, list):", None),
    (SHADOW, "if proc.returncode != 0:", "if proc.returncode == 99:", None),
    (SHADOW, "preexec_fn=lambda: os.nice(10))", "preexec_fn=None)", None),
    (SHADOW, "            if d in _seen:\n                continue", "            if False:\n                continue", None),
    (SHADOW, "            _seen.add(d)\n            say(", "            say(", None),
    (SHADOW, "if len(_seen) >= MAX_LOGGED:", "if len(_seen) >= MAX_LOGGED * 100:", None),
    (SHADOW, "if helper is None or not batch:", "if helper is None:", None),
    (SHADOW, "    if not _clean_noted:\n        _clean_noted = True", "    if True:\n        _clean_noted = True", None),
    (SHADOW, "text = f\"enabled available={int(_helper() is not None)}\" if enabled(settings)", "text = f\"enabled available=1\" if enabled(settings)", None),
    (SHADOW, "if ex is not None and isinstance(exc, ex.Timeout):", "if False:", None),
    (SHADOW, "elif ex is not None and isinstance(exc, ex.ConnectionError):", "elif False:", None),
    (SHADOW, "        except BaseException as exc:\n            try:\n                rec[\"outcome\"]", "        except Exception as exc:\n            try:\n                rec[\"outcome\"]", "KeyboardInterrupt/SystemExit cannot be provoked from the fake"),
    (SHADOW, "            raise\n        try:\n            status", "            pass\n        try:\n            status", None),
    (SHADOW, "        return resp\n\n    def __getattr__", "        return None\n\n    def __getattr__", None),
    (SHADOW, "        return getattr(self._real, name)", "        raise AttributeError(name)", None),
    (SHADOW, "[:300]\n            except Exception:  # noqa: BLE001\n                body = \"\"", "\n            except Exception:  # noqa: BLE001\n                body = \"\"", None),
    (SHADOW, "            if status == 200:\n                try:\n                    resp.json()", "            if True:\n                try:\n                    resp.json()", None),
    (SHADOW, "if \"outcome\" not in c or c.get(\"positional\"):", "if False:", None),
    (SHADOW, "or isinstance(timeout_sec, bool) or not isinstance(timeout_sec, (int, float)):", "or not isinstance(timeout_sec, (int, float)):", "a bool timeout is never passed; the helper declines it too"),
    (SHADOW, "\"headers\": c[\"headers\"], \"timeout\": c[\"timeout\"]}", "\"headers\": c[\"headers\"], \"timeout\": 1}", None),
    # --- the hook in the app
    (APP, '    "net_slice_shadow": True,', '    "net_slice_shadow": False,', None),
    (APP, "    if _NET_SLICE_SHADOW_ON and requests_module is None:", "    if requests_module is None:", None),
    (APP, "                requests_module = rec\n", "                pass\n", None),
    (APP, "    if rec is not None:\n        try:\n            batch", "    if False:\n        try:\n            batch", None),
    (APP, "            batch = net_slice_shadow.probe_batch(base_url, tenant, api_key, timeout_sec, rec.calls, result)", "            batch = net_slice_shadow.probe_batch(base_url, tenant, api_key, timeout_sec, rec.calls, {})", None),
    (APP, "    return result\n\n\ndef _probe_network_sync_status_impl(", "    return None\n\n\ndef _probe_network_sync_status_impl(", None),
    (APP, "            _NET_SLICE_SHADOW_ON = net_slice_shadow.enabled(self.settings)", "            _NET_SLICE_SHADOW_ON = False", None),
]


def tests_for(path):
    if path in (SHADOW, APP):
        return [[PY, "-m", "unittest", "test_net_slice_shadow"]]
    return [["cargo", "test", "--release", "-p", "singws-netslice", "--offline"], ["cargo", "build", "--release", "-p", "singws-netslice", "--offline"],
            [PY, "tools/net_slice_parity.py", "--cases", "400"]]


def run_all(path):
    for cmd in tests_for(path):
        cwd = ROOT / "rust" if cmd[0] == "cargo" else ROOT
        env = dict(ENV, SINGWS_HOME=tempfile.mkdtemp(prefix="mutate_netslice_home_"))
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1500)
        out = p.stdout + p.stderr
        if p.returncode != 0:
            if "error[E" in out or "could not compile" in out:
                return "invalid"
            return "killed"
    return "survived"


def main():
    only = None
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    survivors, invalid = [], []
    for n, (path, old, new, note) in enumerate(M, 1):
        if only and n not in only:
            continue
        original = path.read_text()
        if original.count(old) != 1:
            print(f"#{n}: pattern found {original.count(old)} times in {path.name}: {old[:60]!r}")
            invalid.append(n)
            continue
        path.write_text(original.replace(old, new))
        try:
            verdict = run_all(path)
        finally:
            path.write_text(original)
        tag = {"killed": "killed", "survived": "SURVIVED" + (f" (documented equivalent: {note})" if note else ""), "invalid": "INVALID (does not compile)"}[verdict]
        print(f"#{n} {path.name}: {tag}   {old[:50]!r} -> {new[:40]!r}", flush=True)
        if verdict == "survived" and not note:
            survivors.append(n)
        if verdict == "invalid":
            invalid.append(n)
    subprocess.run(["cargo", "build", "--release", "-p", "singws-netslice", "--offline"], cwd=ROOT / "rust", env=ENV, capture_output=True)
    print(f"\n{len(M)} mutants; undocumented survivors: {survivors or 'none'}; invalid: {invalid or 'none'}")
    return 1 if survivors or invalid else 0


if __name__ == "__main__":
    sys.exit(main())
