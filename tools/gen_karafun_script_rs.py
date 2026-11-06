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

# Python expression inside an f-string -> the Rust variable that carries the same value
EXPR = {
    "query_literal": "query_literal",
    "safe_title_literal": "safe_title_literal",
    "safe_artist_literal": "safe_artist_literal",
    "'TITLE_ONLY' if safe_artist else 'FOUND'": "verdict",
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
    sig = ", ".join(f"{p}: &str" for p in params)
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
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text or (OUT_FOLD.read_text(encoding="utf-8") if OUT_FOLD.exists() else "") != fold:
            print("STALE: rust/crates/singws-karafun/src/script_lines.rs does not match 0.2.18.1.py: run tools/gen_karafun_script_rs.py")
            return 1
        print("script_lines.rs matches the Python source")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    OUT_FOLD.write_text(fold, encoding="utf-8")
    print(f"wrote {OUT} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
