#!/usr/bin/env python3
"""Move inline KaraFun AppleScript lists out of their methods into static builder methods WITHOUT changing them.

  python3 tools/karafun_extract.py apply                       rewrite 0.2.18.1.py (the five simple builders)
  python3 tools/karafun_extract.py check [BASE_REF]            compare, by syntax tree, every builder with the inline list it replaced in git BASE_REF (default HEAD)

`check` is the proof: the moved list must have the SAME syntax tree (same strings, same f-string expressions, same order) as the one in the old source.
The call sites change from `[ ...lines... ]` to `self._karafun_xxx_script()`; nothing else moves."""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "0.2.18.1.py"
# (method that holds the inline list, new builder name)
# (method that holds the inline list, new builder name, builder parameters = the local names the list uses, which keeps the syntax tree identical)
TARGETS = [
    ("_karafun_apple_events_preflight", "_karafun_preflight_script", (), 0),
    ("_karafun_main_window_state", "_karafun_main_window_state_script", (), 0),
    ("_karafun_press_play_control", "_karafun_press_play_script", (), 0),
    ("_karafun_playback_menu_state", "_karafun_playback_menu_state_script", (), 0),
    ("_karafun_raise_results_window", "_karafun_raise_results_window_script", (), 0),
    ("_karafun_read_key_tempo", "_karafun_read_key_tempo_script", (), 0),
    ("_karafun_apply_key_tempo", "_karafun_apply_key_tempo_script", ("key_delta", "tempo_delta", "key_item", "tempo_item"), 0),
    ("_karafun_seek_to", "_karafun_seek_script", ("count", "item"), 0),
    ("_toggle_karafun_playback", "_karafun_toggle_playback_script", (), 0),
    ("_karafun_activate_result_for_playback", "_karafun_double_click_script", ("x", "y"), 0),
    # the big methods; several lists in one method are listed in the order they appear in the source (old_index = position, counted in the OLD source)
    ("_handoff_show_screen_to_karafun", "_karafun_handoff_script", ("x", "y", "width", "height"), 0),
    ("_restore_show_screen_from_karafun", "_karafun_restore_script", (), 0),
    ("_automate_karafun_search_and_play", "_karafun_click_point_script", ("point",), 0),
    ("_automate_karafun_search_and_play", "_karafun_play_now_queue_script", (), 1),
    ("_automate_karafun_search_and_play", "_karafun_adjust_menu_script", ("key_item", "key_steps", "tempo_item", "tempo_steps"), 2),
    ("_automate_karafun_search_and_play", "_karafun_renderer_ready_script", (), 3),
    ("_automate_karafun_search_and_play", "_karafun_start_probe_script", (), 4),
    ("_automate_karafun_search_and_play", "_karafun_set_key_tempo_script", ("requested_key", "requested_tempo"), 5),
    ("_start_karafun_completion_monitor", "_karafun_monitor_probe_script", (), 0),
]


def method_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == name:
                    return sub
    raise SystemExit(f"method {name} not found")


def script_list(func, nth=0):
    found = sorted((n for n in ast.walk(func) if isinstance(n, ast.List) and n.elts and isinstance(n.elts[0], ast.Constant)
                    and isinstance(n.elts[0].value, str) and n.elts[0].value.startswith("tell application")), key=lambda n: n.lineno)
    if nth >= len(found):
        raise SystemExit(f"no list #{nth} in {func.name}")
    return found[nth]


def segment(lines, node):
    """The source text of a node (columns are UTF-8 byte offsets)."""
    if node.lineno == node.end_lineno:
        return lines[node.lineno - 1].encode()[node.col_offset:node.end_col_offset].decode()
    first = lines[node.lineno - 1].encode()[node.col_offset:].decode()
    last = lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode()
    return "\n".join([first] + lines[node.lineno:node.end_lineno - 1] + [last])


def apply():
    text = SRC.read_text(encoding="utf-8")
    for method, builder, params, old_index in TARGETS:
        lines = text.split("\n")
        tree = ast.parse(text)
        func = method_node(tree, method)
        if any(isinstance(n, ast.FunctionDef) and n.name == builder for n in ast.walk(tree)):
            print(f"{builder}: already extracted")
            continue
        done_before = sum(1 for m, b, _p, i in TARGETS if m == method and i < old_index and any(isinstance(n, ast.FunctionDef) and n.name == b for n in ast.walk(tree)))
        node = script_list(func, old_index - done_before)
        seg = segment(lines, node)
        seg_lines = seg.split("\n")
        closing_indent = len(seg_lines[-1]) - len(seg_lines[-1].lstrip())
        shift = 8 - closing_indent
        shifted = [seg_lines[0]] + [(" " * shift + l if shift >= 0 else l[-shift:]) if l.strip() else l for l in seg_lines[1:]]
        new_method = ["    @staticmethod", f"    def {builder}({', '.join(params)}):", "        return " + "\n".join(shifted), ""]
        # call site: replace the list with the call
        head = lines[node.lineno - 1].encode()[:node.col_offset].decode()
        tail = lines[node.end_lineno - 1].encode()[node.end_col_offset:].decode()
        call_lines = lines[:node.lineno - 1] + [head + f"self.{builder}({', '.join(params)})" + tail] + lines[node.end_lineno:]
        # insert the builder just before the method (above its decorators)
        start = (func.decorator_list[0].lineno if func.decorator_list else func.lineno) - 1
        out = call_lines[:start] + "\n".join(new_method).split("\n") + call_lines[start:]
        text = "\n".join(out)
        print(f"{builder}: extracted ({len(node.elts)} lines)")
    SRC.write_text(text, encoding="utf-8")


def check(base="HEAD"):
    old = ast.parse(subprocess.check_output(["git", "show", f"{base}:0.2.18.1.py"], cwd=ROOT).decode("utf-8"))
    new = ast.parse(SRC.read_text(encoding="utf-8"))
    bad = 0
    for method, builder, params, old_index in TARGETS:
        was = script_list(method_node(old, method), old_index)
        func = method_node(new, builder)
        rets = [n for n in func.body if isinstance(n, ast.Return)]
        now = rets[0].value
        same = ast.dump(was) == ast.dump(now)
        calls = f"self.{builder}({', '.join(params)})" in ast.unparse(method_node(new, method))
        sig = [a.arg for a in func.args.args] == list(params)
        print(f"{builder:42} {'IDENTICAL' if same else 'DIFFERENT'}  call site {'ok' if calls else 'MISSING'}  params {'ok' if sig else 'WRONG'}  ({len(was.elts)} lines)")
        bad += (not same) + (not calls) + (not sig)
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["apply"]:
        apply()
    elif sys.argv[1:2] == ["check"]:
        sys.exit(check(*sys.argv[2:3]))
    else:
        raise SystemExit(__doc__)
