#!/usr/bin/env python3
"""Generate rust/crates/singws-karafun/src/script_lines.rs from the REAL KaraFun search script generator in 0.2.18.1.py.

The search script is ~230 fixed AppleScript lines with three inserted values and a few blocks that depend on flags. Copying that by hand is how a
typo ships, so this tool reads the Python source (AST, nothing is executed) and writes the Rust text. The generated file is committed; `--check`
fails when it no longer matches the Python, so a change to the Python script cannot be forgotten on the Rust side.

  python3 tools/gen_karafun_script_rs.py            write the file
  python3 tools/gen_karafun_script_rs.py --check    exit 1 when the committed file is stale
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "0.2.18.1.py"
OUT = ROOT / "rust/crates/singws-karafun/src/script_lines.rs"
OUT_FOLD = ROOT / "rust/crates/singws-karafun/src/casefold_table.rs"
OUT_BUILDERS = ROOT / "rust/crates/singws-karafun/src/builders.rs"

# Python expression inside an f-string -> the Rust variable that carries the same value
EXPR = {
    "query_literal": "query_literal",
    "safe_title_literal": "safe_title_literal",
    "safe_artist_literal": "safe_artist_literal",
    "'TITLE_ONLY' if safe_artist else 'FOUND'": "verdict",
    "title": "title",
    "abs(key_delta)": "key_delta.abs()",
    "abs(tempo_delta)": "tempo_delta.abs()",
    "key_item": "key_item",
    "tempo_item": "tempo_item",
    "count": "count",
    "item": "item",
    "x": "x",
    "y": "y",
    "width": "width",
    "height": "height",
    "int(point[0])": "point.0",
    "int(point[1])": "point.1",
    "key_steps": "key_steps",
    "tempo_steps": "tempo_steps",
    "requested_key": "requested_key",
    "requested_tempo": "requested_tempo",
}


def rs_str(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32:
            raise SystemExit(f"control character {ord(ch)} in a script line: teach the generator")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def line_to_rust(node):
    """One list element: a plain string, or an f-string whose placeholders are known variables."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return f"{rs_str(node.value)}.to_string()"
    if isinstance(node, ast.JoinedStr):
        fmt, args = [], []
        for part in node.values:
            if isinstance(part, ast.Constant):
                fmt.append(part.value.replace("{", "{{").replace("}", "}}"))
            elif isinstance(part, ast.FormattedValue):
                expr = ast.unparse(part.value)
                if expr not in EXPR:
                    raise SystemExit(f"unknown f-string expression in the script: {expr!r}: teach the generator")
                if part.conversion != -1 or part.format_spec is not None:
                    raise SystemExit("f-string conversion/format spec: teach the generator")
                fmt.append("{}")
                args.append(EXPR[expr])
            else:
                raise SystemExit("unexpected f-string part")
        return f'format!({rs_str("".join(fmt))}, {", ".join(args)})'
    raise SystemExit(f"unexpected list element {ast.dump(node)[:80]}")


def block(name, params, list_node, doc):
    body = ",\n        ".join(line_to_rust(e) for e in list_node.elts)
    sig = ", ".join((f"{p[0]}: {p[1]}" if isinstance(p, tuple) else f"{p}: &str") for p in params)
    return f"/// {doc}\npub(crate) fn {name}({sig}) -> Vec<String> {{\n    vec![\n        {body},\n    ]\n}}\n"


def extend_list(stmt):
    assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call), ast.dump(stmt)[:100]
    f = stmt.value.func
    assert isinstance(f, ast.Attribute) and f.attr == "extend" and isinstance(stmt.value.args[0], ast.List)
    return stmt.value.args[0]


def generate():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    func = duration = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KARAFUN_DURATION_HANDLER_LINES" for t in node.targets):
            duration = node.value
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_karafun_search_script":
                    func = sub
    assert func is not None and duration is not None, "the Python script generator moved"
    st = func.body
    # shape of the function; any change in it must be re-read by a person before the Rust side follows
    assert isinstance(st[0], ast.Assign) and isinstance(st[1], ast.Assign) and isinstance(st[2], ast.Assign), "literals changed"
    a = st[3]
    assert isinstance(a, ast.Assign) and isinstance(a.value, ast.List), "base list changed"
    b = st[4]
    assert isinstance(b, ast.If) and ast.unparse(b.test) == "resolve_only" and isinstance(b.body[0], ast.Assign), "resolve_only branch changed"
    c = st[5]
    assert isinstance(c, ast.If) and ast.unparse(c.test) == "require_exact_title and safe_title", "exact-title branch changed"
    assert len(c.body) == 3, "exact-title body changed"
    list_c = extend_list(c.body[0])
    d = c.body[1]
    assert isinstance(d, ast.If) and ast.unparse(d.test) == "safe_artist", "artist branch changed"
    list_d = extend_list(d.body[0])
    list_e = extend_list(c.body[2])
    searched = c.orelse
    assert len(searched) == 1 and ast.unparse(searched[0]) == "lines.append('return \"SEARCHED\"')", "SEARCHED branch changed"
    list_g = extend_list(st[6])
    assert ast.unparse(st[7]) == "result = lines + KARAFUN_DURATION_HANDLER_LINES", "result assignment changed"
    assert isinstance(st[8], ast.Try) and "karafun_script_shadow" in ast.unparse(st[8]), "shadow hook changed"
    assert ast.unparse(st[9]) == "return result" and len(st) == 10, "return changed"
    return a, b, list_c, list_d, list_e, searched, list_g, duration


def render():
    a, b, list_c, list_d, list_e, searched, list_g, duration = generate()
    head = (
        "// @generated by tools/gen_karafun_script_rs.py from KaraokeApp._karafun_search_script in 0.2.18.1.py. DO NOT EDIT BY HAND:\n"
        "// change the Python, then run the tool (`--check` fails while this file is stale).\n"
        "#![allow(clippy::all)]\n\n"
    )
    parts = [
        block("base", ["query_literal"], a.value, "Find the search field, type the query and press Return (the Python `lines = [...]`)."),
        block("resolve_only", [], b.body[0].value, "Recovery: raise the existing results window instead of typing a new search."),
        block("exact_common", [], list_c, "Start of the row scan: element list, window bounds, cutoff."),
        block("artist_pass", ["safe_title_literal", "safe_artist_literal"], list_d, "Row scan that also requires the artist on the same row."),
        block("title_pass", ["safe_title_literal", "verdict"], list_e, "Row scan by title, then the first-result fallback."),
        block("tail", [], list_g, "Closing `end tell` lines."),
        block("duration_handler", [], duration, "KARAFUN_DURATION_HANDLER_LINES."),
    ]
    return head + "\n".join(parts)


# (kind, Python name, Rust name, Rust parameters): the plain `return [...]` builders (see tools/karafun_extract.py)
STATIC = [
    ("method", "_karafun_preflight_script", "preflight", []),
    ("method", "_karafun_main_window_state_script", "main_window_state", []),
    ("method", "_karafun_press_play_script", "press_play", []),
    ("method", "_karafun_playback_menu_state_script", "playback_menu_state", []),
    ("method", "_karafun_raise_results_window_script", "raise_results_window", []),
    ("func", "karafun_preview_probe_script", "preview_probe", []),
    ("method", "_karafun_search_diag_script", "search_diag", ["title"]),
    ("method", "_karafun_read_key_tempo_script", "read_key_tempo", []),
    ("method", "_karafun_toggle_playback_script", "toggle_playback", []),
    ("method", "_karafun_apply_key_tempo_script", "apply_key_tempo", [("key_delta", "i64"), ("tempo_delta", "i64"), ("key_item", "&str"), ("tempo_item", "&str")]),
    ("method", "_karafun_seek_script", "seek", [("count", "i64"), ("item", "&str")]),
    ("method", "_karafun_double_click_script", "double_click", [("x", "i64"), ("y", "i64")]),
    ("method", "_karafun_handoff_script", "handoff", [("x", "i64"), ("y", "i64"), ("width", "i64"), ("height", "i64")]),
    ("method", "_karafun_restore_script", "restore", []),
    ("method", "_karafun_click_point_script", "click_point", [("point", "(i64, i64)")]),
    ("method", "_karafun_play_now_queue_script", "play_now_queue", []),
    ("method", "_karafun_adjust_menu_script", "adjust_menu", [("key_item", "&str"), ("key_steps", "i64"), ("tempo_item", "&str"), ("tempo_steps", "i64")]),
    ("method", "_karafun_renderer_ready_script", "renderer_ready", []),
    ("method", "_karafun_start_probe_script", "start_probe", []),
    ("method", "_karafun_set_key_tempo_script", "set_key_tempo", [("requested_key", "i64"), ("requested_tempo", "i64")]),
    ("method", "_karafun_monitor_probe_script", "monitor_probe", []),
]


def render_builders():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    parts = [
        "// @generated by tools/gen_karafun_script_rs.py from the static builders in 0.2.18.1.py. DO NOT EDIT BY HAND:\n"
        "// change the Python, then run the tool (`--check` fails while this file is stale).\n#![allow(clippy::all)]\n"
    ]
    for kind, pyname, rsname, params in STATIC:
        func = None
        for node in tree.body:
            if kind == "func" and isinstance(node, ast.FunctionDef) and node.name == pyname:
                func = node
            if kind == "method" and isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) and sub.name == pyname:
                        func = sub
        assert func is not None, f"{pyname} not found"
        rets = [n for n in func.body if isinstance(n, ast.Return)]
        assert len(rets) == 1 and isinstance(rets[0].value, ast.List), f"{pyname}: expected a single `return [...]`"
        parts.append(block(rsname, params, rets[0].value, f"{pyname}"))
    return "\n".join(parts)


def render_fold():
    """Python's str.casefold, char by char, where it differs from a plain per-character lower(): the Rust side applies these overrides."""
    rows = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        c = chr(cp)
        cf = c.casefold()
        if cf != c.lower():
            rows.append((c, cf))
    arms = "\n".join(f"        '\\u{{{ord(c):x}}}' => {rs_str(cf)}," for c, cf in rows)
    return ("// @generated by tools/gen_karafun_script_rs.py from Python's str.casefold. DO NOT EDIT BY HAND.\n#![allow(clippy::all)]\n\n"
            "/// The casefold of one character when it differs from its plain lowercase, else None.\n"
            "pub(crate) fn special(c: char) -> Option<&'static str> {\n    Some(match c {\n" + arms + "\n        _ => return None,\n    })\n}\n")


def main():
    text = render()
    fold = render_fold()
    builders = render_builders()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text or (OUT_FOLD.read_text(encoding="utf-8") if OUT_FOLD.exists() else "") != fold or (OUT_BUILDERS.read_text(encoding="utf-8") if OUT_BUILDERS.exists() else "") != builders:
            print("STALE: rust/crates/singws-karafun/src/script_lines.rs does not match 0.2.18.1.py: run tools/gen_karafun_script_rs.py")
            return 1
        print("script_lines.rs matches the Python source")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    OUT_FOLD.write_text(fold, encoding="utf-8")
    OUT_BUILDERS.write_text(builders, encoding="utf-8")
    print(f"wrote {OUT} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
