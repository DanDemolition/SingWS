"""Stage D4 shadow hook: compares what ``requests`` ACTUALLY sent with what the Rust request builder (``singws-wire``) would send, and LOGS differences.

SHADOW ONLY, and Rust sends nothing: the Rust library only computes the URL, body and length headers for the same inputs. It is called from
``network_lifecycle.ShutdownRequests`` (the single choke point every ``requests.get/post`` of the app goes through) AFTER the real response exists,
on the calling thread, so it cannot change, delay or fail a request. Setting ``wire_shadow`` (default off until its package gate passes).

Privacy: only the endpoint file name (e.g. ``delete_remote_request.php``) is ever logged. Never the host, query string, body, headers or any value
(requests carry the tenant and API key).

What is compared, against the request that really went on the wire (the FIRST request when redirects happened): URL, body bytes, and the
Content-Type / Content-Length headers (skipped when the caller set that header itself). Calls Rust does not claim to reproduce are counted as
``declined`` and never as mismatches: multipart ``files=``, non-dict ``data=``, values that are not JSON, bodies over MAX_BODY, and URLs ``requests``
rewrites.

Log lines (grep ``WIRE-SHADOW``): ``enabled``, ``decision endpoint=<file> kind=<get|form|json|post_empty> agree=1`` (first per pair),
``declined endpoint=<file> reason=<...>`` (first per pair), ``MISMATCH endpoint=<file> kind=<..> fields=<..> ...``, ``summary`` every SUMMARY_EVERY.
"""

from __future__ import annotations

import ctypes
import json
import threading
from urllib.parse import urlsplit

SUMMARY_EVERY = 100
MAX_BODY = 256 * 1024  # larger bodies (the singer-history sync) are not re-built: the check would cost more than it proves


class WireShadow:
    def __init__(self, log, lib=None):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self.checks = 0
        self.mismatches = 0
        self.declined = 0
        self._seen = set()
        self._lock = threading.Lock()

    def _say(self, text):
        try:
            self._log(f"[WIRE-SHADOW] {text}")
        except Exception:
            pass

    # ---- the observer installed on ShutdownRequests ------------------------------------------------------------------------------------
    def observe(self, name, args, kwargs, response):
        """``ShutdownRequests`` observer: (method name, positional args, keyword args, completed response). Never raises."""
        try:
            if not self.available:
                return None
            url = args[0] if args else kwargs.get("url")
            request = getattr(response, "request", None)
            history = getattr(response, "history", None) or []
            sent = history[0].request if history else request
            if not isinstance(url, str) or sent is None:
                return None
            return self.check(name.upper(), url, kwargs, sent)
        except Exception as exc:
            self._say(f"observe error: {exc!r}")
            return None

    # ---- comparison --------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def endpoint_of(url):
        try:
            path = urlsplit(url).path
        except ValueError:
            return "(bad-url)"
        return path.rsplit("/", 1)[-1] or "(root)"

    def _decline(self, endpoint, reason):
        with self._lock:
            self.declined += 1
            first = (endpoint, "declined:" + reason) not in self._seen
            self._seen.add((endpoint, "declined:" + reason))
        if first:
            self._say(f"declined endpoint={endpoint} reason={reason}")
        return None

    def _call(self, request):
        payload = json.dumps(request, allow_nan=False).encode("utf-8")
        cap = len(payload) * 8 + 4096
        buf = ctypes.create_string_buffer(cap)
        n = self._lib.singws_wire_json(payload, buf, cap)
        if n < 0:
            raise RuntimeError(f"wire call failed code={n}")
        return json.loads(buf.raw[:n].decode("utf-8"))

    def check(self, method, url, kwargs, sent):
        """Compare the Rust-built request with ``sent`` (a requests.PreparedRequest). Returns True/False, or None when declined/skipped."""
        endpoint = self.endpoint_of(url)
        if kwargs.get("files"):
            return self._decline(endpoint, "multipart")
        data, params, body_json = kwargs.get("data"), kwargs.get("params"), kwargs.get("json")
        for label, value in (("data", data), ("params", params)):
            if value is not None and not isinstance(value, (dict, list, tuple)):
                return self._decline(endpoint, f"{label}_type")
        sent_body = sent.body
        if isinstance(sent_body, (bytes, bytearray)):
            sent_body = bytes(sent_body).decode("utf-8", "surrogateescape")
        elif sent_body is not None and not isinstance(sent_body, str):
            return self._decline(endpoint, "streamed_body")
        if sent_body is not None and len(sent_body) > MAX_BODY:
            return self._decline(endpoint, "large_body")
        try:
            out = self._call({"method": method, "url": url, "params": params, "data": data, "json": body_json})
        except (TypeError, ValueError):
            return self._decline(endpoint, "not_json")
        if not out.get("ok"):
            return self._decline(endpoint, "rust_" + str(out.get("error", "?"))[:30].replace(" ", "_"))

        user_headers = {str(k).lower() for k in (kwargs.get("headers") or {})}
        sent_headers = sent.headers
        diffs = []
        if sent.url != out["url"]:
            diffs.append("url")
        if sent_body != out["body"]:
            diffs.append("body")
        if "content-type" not in user_headers and sent_headers.get("Content-Type") != out["content_type"]:
            diffs.append("content_type")
        if "content-length" not in user_headers:
            length = sent_headers.get("Content-Length")
            if (int(length) if length is not None else None) != out["content_length"]:
                diffs.append("content_length")

        kind = "get" if method == "GET" else ("json" if (not data and body_json is not None) else ("form" if data else "post_empty"))
        with self._lock:
            self.checks += 1
            if diffs:
                self.mismatches += 1
            first = (endpoint, kind) not in self._seen
            self._seen.add((endpoint, kind))
            summary = self.checks % SUMMARY_EVERY == 0
            checks, mismatches = self.checks, self.mismatches
        if diffs:
            at = ""
            if "body" in diffs:
                a, b = sent_body or "", out["body"] or ""
                at = f" first_body_difference_at={next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))} sent_len={len(a)} rust_len={len(b)}"
            self._say(f"MISMATCH endpoint={endpoint} kind={kind} fields={','.join(diffs)}{at}")
        elif first:
            self._say(f"decision endpoint={endpoint} kind={kind} agree=1")
        if summary:
            self._say(f"summary checks={checks} mismatches={mismatches} declined={self.declined}")
        return not diffs


def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_wire_json  # AttributeError if this dylib predates the wire export
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
