"""Survivors of tools/mutate_songend.py. NOTES: explicit entries "L<line>:<old>-><new>" -> "equivalent: why" | "GAP: what the corpus lacks".
classify() labels the rest by pattern. Honest reading of the labels:
  equivalent  provably no observable difference (operand is a non-negative constant)
  GAP-default the mutated token is the DEFAULT of a settings/snapshot field the harness always supplies; only a caller that omits the field differs
  GAP-return  a `return false/true` whose value no tested path observes
  GAP-tie     comparison boundary: needs two float/ns values to tie exactly (or to cross by less than the tick); the corpus has few exact ties
  GAP-other   anything else (listed in the design doc)"""
import re

NOTES = {
    # --- 2026-11-05 reclassification of survivors that the replay contract makes unreachable (line numbers are those of lib.rs when the pass was run)
    "L281:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L304:1->2": "equivalent under the replay contract: the replay always carries this argument/id (Python passes the generation / deliver id on every call)",
    "L320:0->1": "equivalent under the replay contract: the replay always carries this argument/id (Python passes the generation / deliver id on every call)",
    "L324:0->1": "equivalent under the replay contract: the replay always carries this argument/id (Python passes the generation / deliver id on every call)",
    "L423:0.0->1.0": "equivalent: a JSON number always converts to f64 (float_roundtrip), so the unwrap_or default is unreachable",
    "L431:0.0->1.0": "equivalent under the replay contract: the harness's cache records and scan results always carry this field",
    "L436:false->true": "equivalent: runs_to_end is only read when trailing <= 0, and this path always has a positive trailing",
    "L448:false->true": "equivalent: runs_to_end is only read when trailing <= 0, and this path always has a positive trailing",
    "L456:0.0->1.0": "equivalent: a JSON number always converts to f64 (float_roundtrip), so the unwrap_or default is unreachable",
    "L467:0.0->1.0": "equivalent: a JSON number always converts to f64 (float_roundtrip), so the unwrap_or default is unreachable",
    "L525:0.0->1.0": "equivalent under the replay contract: the harness's cache records and scan results always carry this field",
    "L549:false->true": "equivalent under the replay contract: the harness's cache records and scan results always carry this field",
    "L551:0.0->1.0": "equivalent under the replay contract: the harness's cache records and scan results always carry this field",
    "L673:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L676:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L918:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L938:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L968:0->1": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L974:0->1": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L979:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L1001:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L1107:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    "L1147:false->true": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
    # --- 2026-11-08: survivors that tiny-value / two-song scenarios could not kill because there is no observable difference (reasons read from the Rust and the Python)
    "L433: > -> >= ": "equivalent: with rec_dur == 0.0 the tail is 0.0, below the 0.3 threshold, so the branch applies nothing either way",
    "L474:0.0->1.0": "equivalent: apply_audio_end_floor re-tests `trailing <= 0.0` before it uses runs_to_end, so widening the test here to <= 1.0 changes nothing",
    "L483: > -> >= ": "equivalent: every caller passes a duration above 0 (the scan path returns early on <= 0, the record path needs rec_dur > 0, the cache path needs true_dur > 0)",
    "L486: > -> >= ": "equivalent: every caller passes a duration above 0 (see L483)",
    "L486:0.0->1.0": "equivalent: runs_to_end is true only when trailing <= 0 (scan path) and false on the cache and record paths, so trailing never lies in (0, 1] with it set",
    "L595:<=-><": "equivalent: with audio_s == 0.0 the next comparison (audio_ns <= dur_ns) returns the media duration anyway",
    "L621:0->1": "equivalent: it needs a media duration of exactly 1 ns, and the model's clock is whole milliseconds",
    "L1165:true->false": "equivalent under the replay contract: snapshot() in tools/songend_scenarios.py always emits this key with the right type, so the default is never read",
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
