#!/usr/bin/env python3
"""Find the 'missing nonlocal' bug class: a nested function assigns (plain `=` / `+=`, not as a loop/with/except target) a name that its ENCLOSING function sets BEFORE the nested
function is defined and READS after it, without declaring it `nonlocal`/`global`. The assignment then creates a throw-away local and the outer variable never changes (found
2026-11-06 in the KaraFun completion monitor: `playback_clock_origin` was assigned inside `_confirm_playback` without `nonlocal`, so the fallback end clock was never rebased).

It reports CANDIDATES for a human to read; some are deliberate (the nested function wants its own local, or the name is a scratch variable reused by accident but never meant to
be shared). Run over the app modules: python3 tools/find_missing_nonlocal.py [FILE ...]. Exit status 0 always (a review aid, not a gate)."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def own_nodes(fn):
    """Every node in this function's own scope (not inside nested functions/lambdas/classes)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, SCOPES):
            stack.extend(ast.iter_child_nodes(n))


def plain_assign_targets(fn):
    """name -> first line, for names bound by a plain Assign/AugAssign/AnnAssign in the function's own scope."""
    out = {}
    for n in own_nodes(fn):
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets = [n.target]
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    out.setdefault(sub.id, []).append(n.lineno)
    return out


def any_store_lines(fn):
    """name -> lines of EVERY binding in the function's own scope (assignment, loop/with/except targets): used to see that the enclosing function rebinds the name itself."""
    out = {}
    for n in own_nodes(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.setdefault(n.id, []).append(n.lineno)
    return out


def declared(fn):
    names = set()
    for n in own_nodes(fn):
        if isinstance(n, (ast.Nonlocal, ast.Global)):
            names.update(n.names)
    return names


def loads_in(fn):
    """name -> lines of every read in this function's OWN scope (a read inside another nested function usually reads that function's own copy)."""
    out = {}
    for n in own_nodes(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.setdefault(n.id, []).append(n.lineno)
    return out


def params(fn):
    a = fn.args
    return {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs} | ({a.vararg.arg} if a.vararg else set()) | ({a.kwarg.arg} if a.kwarg else set())


def nested_defs(fn):
    found, stack = [], list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(n)
        elif not isinstance(n, (ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))
    return found


def scan(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    out = []

    def visit(fn):
        outer_assigns = plain_assign_targets(fn)
        outer_stores = any_store_lines(fn)
        outer_loads = loads_in(fn)
        for child in nested_defs(fn):
            end = getattr(child, "end_lineno", child.lineno)
            inner = plain_assign_targets(child)
            skip = declared(child) | params(child)
            for name, lines in inner.items():
                if name in skip or name not in outer_assigns:
                    continue
                set_before = [ln for ln in outer_assigns[name] if ln < child.lineno]
                read_after = [ln for ln in outer_loads.get(name, []) if ln > end]
                # the enclosing function setting the name again between the nested def and the read makes the read independent of the nested function
                reset_between = [ln for ln in outer_stores.get(name, []) if end < ln < (read_after[0] if read_after else end)]
                if set_before and read_after and not reset_between:
                    out.append((child.lineno, fn.name, child.name, name, lines[0], read_after[0]))
            visit(child)

    nested_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for c in nested_defs(node):
                nested_ids.add(id(c))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in nested_ids:
            visit(node)
    return sorted(set(out))


def main(argv):
    files = argv or [str(p) for p in sorted(ROOT.glob("*.py")) if not p.name.startswith("test_") and p.name not in ("conftest.py", "_test_profile.py")]
    total = 0
    for f in files:
        for line, outer, inner, name, assign_line, read_line in scan(f):
            total += 1
            print(f"{Path(f).name}:{line}: {outer} -> {inner} assigns '{name}' (line {assign_line}) without nonlocal; {outer} reads it again at line {read_line}")
    print(f"{total} candidate(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
