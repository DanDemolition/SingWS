"""Survivors of tools/mutate_songend.py. NOTES: explicit entries "L<line>:<old>-><new>" -> "equivalent: why" | "GAP: what the corpus lacks".
classify() labels the rest by pattern. Honest reading of the labels:
  equivalent  provably no observable difference (operand is a non-negative constant)
  GAP-default the mutated token is the DEFAULT of a settings/snapshot field the harness always supplies; only a caller that omits the field differs
  GAP-return  a `return false/true` whose value no tested path observes
  GAP-tie     comparison boundary: needs two float/ns values to tie exactly (or to cross by less than the tick); the corpus has few exact ties
  GAP-other   anything else (listed in the design doc)"""
import re

NOTES = {
    # the `runs_to_end` argument only matters when trailing <= 0; both cache paths call apply_audio_end_floor with a trailing > 0
    # (library record: tail >= 0.3; trailing cache: c > 0.0), so flipping the flag there cannot change anything
    "L410:false->true": "equivalent: runs_to_end is only read when trailing <= 0 and this path always has tail >= 0.3",
    "L422:false->true": "equivalent: runs_to_end is only read when trailing <= 0 and this path requires c > 0.0",
}


def classify(snippet, old, new):
    s = snippet
    if ".max(0)" in s and old == "0":
        return "equivalent: max(0) of a non-negative constant/argument"
    if re.search(r"unwrap_or\(|self\.s[bf]\(", s) and old in ("false", "true", "0", "0.0", "0.8", "5.0", "2.0", "1", "45.0", "-1"):
        return "GAP-default: default of a field the harness always supplies"
    if s.startswith("return ") or s == "true":
        return "GAP-return: returned value not observed on a tested path"
    if old in ("<=", ">=", " > ", " < ", "0", "0.0", "0.5", "45.0") :
        return "GAP-tie: boundary / zero-value tie"
    if old in ("||", "&&"):
        return "GAP-other: connective; needs a case where exactly one operand differs"
    return ""
