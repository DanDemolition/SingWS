#!/usr/bin/env python3
"""Stage D4 groundwork: inventory every HTTP call the app makes, from the source, so a Rust client has a written contract to match.

Reads 0.2.18.1.py (and the other app modules) with ``ast``; sends NOTHING and opens no sockets. For each ``requests.get(...)`` /
``requests.post(...)`` call it records the enclosing function, the endpoint path (the ``*.php`` string in the URL expression), which
keyword arguments are used (``json=``, ``data=``, ``params=``, ``headers=``, ``files=``, ``verify=`` ...), and the timeout expression,
then prints a table grouped by endpoint plus the facts that decide how risky a port is: calls with no timeout, calls that disable TLS
verification, endpoints used from several places, and calls whose URL is not a ``*.php`` API path.

Usage:  python3 tools/network_inventory.py [--markdown] [FILE ...]      (default: 0.2.18.1.py)
Exit status 0 always (an inventory is a report); the tests pin the facts that must not silently change.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

METHODS = {"get", "post", "put", "delete", "patch", "head", "request"}
_PATH = re.compile(r"(/?(?:api/v1/)?[A-Za-z0-9_\-]+\.php)")


def _src(node, limit=90):
    try:
        text = ast.unparse(node)
    except Exception:
        return "?"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _endpoint(url_node, assigns):
    """Best-effort endpoint path from a URL expression (string pieces, f-strings, or a variable assigned from them)."""
    pieces = []
    for sub in ast.walk(url_node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            pieces.append(sub.value)
    text = "".join(pieces)
    m = _PATH.search(text)
    if m:
        return m.group(1).split("/")[-1]
    if isinstance(url_node, ast.Name) and url_node.id in assigns:
        return _endpoint(assigns[url_node.id], {})
    return None


class Visitor(ast.NodeVisitor):
    def __init__(self, file):
        self.file = file
        self.stack, self.calls = [], []
        self.assigns = [{}]

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.assigns.append({})
        self.generic_visit(node)
        self.assigns.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            self.assigns[-1][node.targets[0].id] = node.value
        self.generic_visit(node)

    def visit_Call(self, node):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in METHODS and isinstance(fn.value, ast.Name) and fn.value.id == "requests":
            kwargs = {k.arg: k.value for k in node.keywords if k.arg}
            url = node.args[0] if node.args else kwargs.get("url")
            scope = {}
            for frame in self.assigns:
                scope.update(frame)
            endpoint = _endpoint(url, scope) if url is not None else None
            verify = kwargs.get("verify")
            self.calls.append({
                "file": self.file, "line": node.lineno, "function": ".".join(self.stack[-2:]) or "<module>", "method": fn.attr.upper(),
                "endpoint": endpoint, "url": _src(url) if url is not None else "?",
                "kwargs": sorted(kwargs), "timeout": _src(kwargs["timeout"]) if "timeout" in kwargs else None,
                "verify_disabled": isinstance(verify, ast.Constant) and verify.value is False,
                "body": "json" if "json" in kwargs else ("data" if "data" in kwargs else ("files" if "files" in kwargs else None)),
            })
        self.generic_visit(node)


def inventory(paths):
    calls = []
    for path in paths:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
        v = Visitor(Path(path).name)
        v.visit(tree)
        calls.extend(v.calls)
    return calls


def facts(calls):
    by_ep = defaultdict(list)
    for c in calls:
        by_ep[c["endpoint"] or "(non-API url)"].append(c)
    return {
        "total": len(calls),
        "get": sum(c["method"] == "GET" for c in calls),
        "post": sum(c["method"] == "POST" for c in calls),
        "endpoints": sorted(k for k in by_ep if k != "(non-API url)"),
        "no_timeout": [c for c in calls if c["timeout"] is None],
        "verify_disabled": [c for c in calls if c["verify_disabled"]],
        "non_api": by_ep.get("(non-API url)", []),
        "multi_site": {k: v for k, v in by_ep.items() if len(v) > 1 and k != "(non-API url)"},
        "by_endpoint": by_ep,
    }


def render(calls, markdown=False):
    f = facts(calls)
    out = []
    h = (lambda t: f"## {t}") if markdown else (lambda t: f"== {t}")
    out.append(h(f"HTTP calls: {f['total']} ({f['get']} GET, {f['post']} POST) to {len(f['endpoints'])} API endpoints"))
    out.append("")
    out.append(h("By endpoint"))
    for ep in sorted(f["by_endpoint"]):
        sites = f["by_endpoint"][ep]
        methods = "/".join(sorted({c["method"] for c in sites}))
        bodies = ",".join(sorted({c["body"] for c in sites if c["body"]})) or "-"
        kw = ",".join(sorted({k for c in sites for k in c["kwargs"] if k not in ("timeout",)})) or "-"
        out.append(f"- `{ep}`  {methods}  sites={len(sites)}  body={bodies}  kwargs={kw}" if markdown
                   else f"{ep:38s} {methods:9s} sites={len(sites):<2d} body={bodies:<10s} kwargs={kw}")
        for c in sites:
            out.append(f"    {c['file']}:{c['line']} {c['function']}  timeout={c['timeout']}")
    out.append("")
    out.append(h("Risk facts for a port"))
    out.append(f"- calls with NO timeout: {len(f['no_timeout'])}" + "".join(f"\n    {c['file']}:{c['line']} {c['function']} {c['method']} {c['endpoint'] or c['url']}" for c in f["no_timeout"]))
    out.append(f"- calls that disable TLS verification: {len(f['verify_disabled'])}" + "".join(f"\n    {c['file']}:{c['line']} {c['function']}" for c in f["verify_disabled"]))
    out.append(f"- endpoints called from several places: {len(f['multi_site'])}: " + ", ".join(f"{k} x{len(v)}" for k, v in sorted(f["multi_site"].items())))
    out.append(f"- calls whose URL is not a *.php API path: {len(f['non_api'])}" + "".join(f"\n    {c['file']}:{c['line']} {c['function']} {c['method']} {c['url']}" for c in f["non_api"]))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    files = args.files or [root / "0.2.18.1.py"]
    print(render(inventory(files), markdown=args.markdown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
