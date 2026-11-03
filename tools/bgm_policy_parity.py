#!/usr/bin/env python3
"""Parity: the real Python BackgroundMusicPlayer vs the Rust ``singws-bgm-policy`` crate, scripted + seeded-random scenarios.

For every scenario the player runs under tools/bgm_policy_scenarios.py (recording fake engine, virtual clock); the recorded inputs
are replayed through the Rust CLI and every command (engine calls, host calls, timers, worker spawns), the return value and the
player's state after every event must be IDENTICAL (strict types: 5000 is not 5000.0).

  .venv/bin/python tools/bgm_policy_parity.py [--seeds N] [--binary PATH] [--mutant-run]   (needs PyQt6; SINGWS_HOME is a scratch dir)
Build first:  CARGO_TARGET_DIR=~/.cache/singws-rust-target-bgm-policy cargo build --release -p singws-bgm-policy   (in rust/)
Exit status 0 only when every scenario is identical.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bgm_policy_scenarios as sc  # noqa: E402

DEFAULT_BIN = Path.home() / ".cache/singws-rust-target-bgm-policy/release/singws-bgm-policy"

BASE_SETTINGS = {"simple_audio_mode": True, "bg_volume": 0.8, "seamless_transitions_enabled": True,
                 "bg_end_silence_trim_enabled": False, "bg_normalize_enabled": True, "bgm_fade_in_floor_db": -60.0}

REC_DEAD_TAIL = {"audio_start": 0.5, "audio_end": None, "fade_confidence": 0.0}      # audio_end filled per track
LOUD = {"gain_linear": 1.7, "gain_db": 4.6}


def track(name, dur, record=None, **kw):
    t = {"name": name, "duration": dur}
    if record is not None:
        t["record"] = record
    t.update(kw)
    return t


def scen(name, tracks, script, run_s, **kw):
    s = {"name": name, "settings": dict(BASE_SETTINGS), "tracks": tracks, "script": script, "run_ms": int(run_s * 1000)}
    s["settings"].update(kw.pop("settings", {}))
    s.update(kw)
    return s


def at(sec, ev, **a):
    return {"at": int(sec * 1000), "ev": ev, "a": a}


def setw(sec, **a):
    return {"at": int(sec * 1000), "ev": "set", "a": a}


def scripted():
    out = []
    three = [track("a", 20.0), track("b", 20.0), track("c", 20.0)]
    out.append(scen("autoplay_wraps_playlist", three, [at(0, "play")], 75))
    out.append(scen("crossfade_ms_setting_bounds", three, [at(0, "play")], 60, crossfade_ms=1500))
    out.append(scen("crossfade_ms_setting_bounds_high", three, [at(0, "play")], 60, crossfade_ms=20000))
    recs = [
        track("n1", 30.0, {"audio_start": 0.4, "audio_end": 28.0, "fade_start": 22.0, "fade_confidence": 0.9}),
        track("n2", 30.0, {"audio_start": 2.0, "audio_end": 25.0, "fade_start": None, "fade_confidence": 0.1}),
        track("n3", 30.0, {"audio_start": 0.0, "audio_end": 29.8, "fade_start": None, "fade_confidence": 0.5}),
        track("n4", 30.0, {"audio_start": 0.0, "audio_end": 30.0, "analysis_version": 7}),
    ]
    out.append(scen("analysis_records", recs, [at(0, "play")], 130))
    out.append(scen("adaptive_rounding_tie", [track("r1", 29.0, {"audio_start": 0.0, "audio_end": 28.0625, "fade_start": 24.0, "fade_confidence": 0.9}), track("r2", 29.0)],
                    [at(0, "play")], 60))
    out.append(scen("analysis_records_lead", recs, [at(0, "play")], 130, lead_s=0.35))
    out.append(scen("seamless_disabled", recs, [at(0, "play")], 100, settings={"seamless_transitions_enabled": False}))
    out.append(scen("duration_mismatch_record", [track("m1", 30.0, {"duration": 33.0, "audio_end": 20.0}), track("m2", 30.0)], [at(0, "play")], 80))
    out.append(scen("manual_next_prev", three, [at(0, "play"), at(3, "next"), at(4, "next"), at(12, "prev"), at(13, "prev"), at(14, "next")], 40))
    out.append(scen("next_when_stopped", three, [at(1, "next"), at(2, "prev"), at(3, "play"), at(9, "pause"), at(10, "next"), at(11, "play")], 25))
    out.append(scen("pause_resume_stop", three, [at(0, "play"), at(4, "pause"), at(6, "play"), at(9, "stop"), at(11, "play"), at(13, "stop_with_fade"),
                                                 at(14.5, "play"), at(16, "stop_with_fade", ms=2000), at(16.5, "play"), at(20, "stop_with_fade", ms=0)], 40))
    out.append(scen("stop_after_current", [track("a", 12.0), track("b", 12.0)], [at(0, "play"), at(1, "stop_after", v=True), at(30, "play"), at(31, "stop_after", v=False)], 60))
    out.append(scen("stop_after_current_toggled_off", [track("a", 12.0), track("b", 12.0)], [at(0, "play"), at(1, "stop_after", v=True), at(5, "stop_after", v=False)], 60))
    out.append(scen("late_ticks_stall", three, [at(0, "play")], 60, tick_skips=[[10, 24]]))
    out.append(scen("source_ended_without_crossfade", [track("a", 6.0), track("b", 6.0)], [at(0, "play")], 40, tick_skips=[[4, 7]]))
    out.append(scen("source_ended_crossfade_refused", [track("a", 6.0), track("b", 6.0)],
                    [at(0, "play"), setw(1, responses={"start_crossfade": False})], 40, tick_skips=[[4, 7]]))
    out.append(scen("fade_out_in_karaoke_cycle", three, [at(0, "play"), at(3, "fade_out", ms=1000), setw(3.0, karaoke=True), at(8, "fade_in", target=None, ms=3000, allow=True),
                                                         setw(8.1, karaoke=False), at(12, "fade_out", ms=500), at(13, "fade_in", target=0.5, ms=1500)], 30, settle_ms=120))
    out.append(scen("karaoke_active_blocks_play", three, [setw(0, karaoke=True), at(1, "play"), at(2, "fade_in", ms=1000), at(3, "fade_in", ms=1000, allow=True), at(8, "fade_out")], 15))
    out.append(scen("fade_out_cancelled_by_play", three, [at(0, "play"), at(2, "fade_out", ms=2000), at(3, "play")], 12))
    out.append(scen("fade_out_cancelled_by_stop_fade", three, [at(0, "play"), at(2, "fade_out", ms=2000), at(3, "stop_with_fade", ms=1000)], 12))
    out.append(scen("ensure_audible", three, [at(0, "play"), at(2, "pause"), at(3, "ensure_audible"), at(4, "play"), at(5, "ensure_audible"),
                                              setw(5.5, live_volume=0.4), at(6, "fade_out", ms=500), at(7, "ensure_audible")], 12))
    out.append(scen("cancel_pending_and_volume", three, [at(0, "play"), at(2, "set_volume", v=1.7), at(3, "set_volume", v=-0.2), at(4, "cancel_pending", restore=True),
                                                         setw(4.5, live_volume=0.33), at(5, "cancel_pending", restore=True), at(6, "cancel_pending", restore=False),
                                                         at(7, "enabled", v=False), at(8, "enabled", v=True), at(9, "init_volume")], 12))
    out.append(scen("crossfade_cancelled_midway", three, [at(0, "play"), at(15.2, "cancel_pending", restore=True), at(16, "next")], 40))
    out.append(scen("next_during_crossfade_ignored", three, [at(0, "play"), at(16, "next"), at(17, "prev")], 30))
    out.append(scen("fade_out_during_crossfade", three, [at(0, "play"), at(16, "fade_out", ms=800), at(20, "play")], 30))
    out.append(scen("crossfade_refused_falls_back", three, [at(0, "play"), setw(1, responses={"start_crossfade": False})], 50))
    out.append(scen("crossfade_raises_unplayable", three, [at(0, "play"), setw(1, responses={"start_crossfade": {"raise": "BASS error 2: cannot open"}})], 50))
    out.append(scen("crossfade_raises_fileopen_name", three, [at(0, "play"), setw(1, responses={"start_crossfade": {"raise": "BASS_ERROR_FILEOPEN"}})], 50))
    out.append(scen("preload_raises_fileopen_name", three, [at(0, "play")], 50, prepare_results={"b": ["error BASS_ERROR_FILEOPEN here"]}))
    out.append(scen("crossfade_raises_transient", three, [at(0, "play"), setw(1, responses={"start_crossfade": {"raise": "device busy"}})], 50))
    out.append(scen("complete_crossfade_refused", three, [at(0, "play"), setw(1, responses={"complete_crossfade": False})], 50))
    out.append(scen("play_refused", three, [setw(0, responses={"play": False}), at(1, "play"), at(2, "fade_in", ms=500), setw(3, responses={"play": None}), at(4, "play")], 12))
    out.append(scen("load_raises", three, [setw(0, responses={"load": {"raise": "boom"}}), at(1, "play"), at(2, "preload"), at(3, "fade_in", ms=500),
                                           setw(4, responses={"load": None}), at(5, "preload"), at(6, "preload"), at(7, "play")], 12))
    out.append(scen("preload_then_play_with_realign", three, [at(0, "preload"), at(1, "playlist", paths=["@b", "@a"], index=1, realign=True), at(2, "play")], 6))
    out.append(scen("preload_error_permanent_skips_track", three,
                    [at(0, "play")], 60, prepare_results={"b": ["BASS error 2: file open"]}))
    out.append(scen("preload_error_transient_retries", three, [at(0, "play")], 60, prepare_results={"b": ["network glitch", "network glitch"]}))
    out.append(scen("file_deleted_then_preload", three, [at(0, "play"), setw(0.2, files={"@b": "delete"})], 60))
    out.append(scen("file_changes_becomes_eligible", three, [at(0, "play"), setw(0.2, prepare_results={"@b": ["BASS error 2: open"]}), setw(30, files={"@b": "touch"})], 100))
    out.append(scen("all_tracks_unplayable", [track("a", 20.0), track("b", 20.0)], [at(0, "play"), setw(0.1, files={"@a": "delete", "@b": "delete"}), at(5, "next")], 40))
    out.append(scen("single_track_playlist", [track("only", 15.0)], [at(0, "play")], 60))
    out.append(scen("preload_ui_delay_race", three, [at(0, "play"), at(0.45, "next")], 20, preload_latency_ms=700))
    out.append(scen("startup_preload_async", three, [at(0, "preload_async"), at(0.01, "play"), at(2, "preload_async")], 12))
    out.append(scen("startup_preload_with_fade_resume", three, [at(0, "preload_async"), at(0.01, "fade_in", ms=2500)], 12, preload_latency_ms=100))
    out.append(scen("startup_preload_stale_by_stop", three, [at(0, "preload_async"), at(0.01, "play"), at(0.02, "stop")], 8))
    out.append(scen("startup_preload_error", three, [at(0, "preload_async"), at(0.01, "play")], 8, prepare_results={"a": ["bad file"]}))
    out.append(scen("startup_preload_install_refused", three, [at(0, "preload_async"), at(0.01, "play"), setw(0, responses={"install_prepared_primary": False})], 8))
    out.append(scen("startup_preload_playlist_changed", three, [at(0, "preload_async"), at(0.01, "playlist", paths=["@b", "@c"], index=0)], 8, preload_latency_ms=100))
    out.append(scen("normalization_on", three, [at(0, "play"), at(1, "fade_in", ms=500)], 50,
                    settings={"simple_audio_mode": False, "bg_normalize_enabled": True},
                    loud_by_name={"a": LOUD, "b": {"gain_linear": 9.0, "gain_db": 20}, "c": {"gain_linear": 0.0}}))
    out.append(scen("normalization_gain_key_missing", three, [at(0, "play"), at(2, "next")], 30,
                    settings={"simple_audio_mode": False}, loud_by_name={"a": {"gain_db": 3.0}, "b": {"gain_db": 3.0}, "c": {"gain_db": 3.0}}))
    out.append(scen("normalization_off_flags", three, [at(0, "play")], 30, settings={"simple_audio_mode": False, "bg_normalize_enabled": False}))
    out.append(scen("normalization_unknown_cache", three, [at(0, "play"), at(1, "next")], 30, settings={"simple_audio_mode": False}))
    out.append(scen("silence_tail_advance", [track("a", 40.0), track("b", 40.0)], [at(0, "play"), setw(1, meter=0.0)], 60,
                    settings={"bg_end_silence_trim_enabled": True}))
    out.append(scen("silence_not_in_tail", [track("a", 200.0), track("b", 40.0)], [at(0, "play"), setw(1, meter=0.0)], 60,
                    settings={"bg_end_silence_trim_enabled": True}))
    out.append(scen("silence_interrupted_by_noise", [track("a", 40.0), track("b", 40.0)],
                    [at(0, "play"), setw(1, meter=0.0), setw(2.2, meter=0.9), setw(3.0, meter=0.0)], 60, settings={"bg_end_silence_trim_enabled": True}))
    out.append(scen("silence_with_tick_gap", [track("a", 40.0), track("b", 40.0)], [at(0, "play"), setw(1, meter=0.0)], 60,
                    settings={"bg_end_silence_trim_enabled": True}, tick_skips=[[3, 7]]))
    out.append(scen("fade_in_floor_attach", three, [at(0, "attach"), setw(1, settings={"bgm_fade_in_floor_db": -40.0}), at(2, "attach"), setw(3, settings={"bgm_fade_in_floor_db": -60.0}), at(4, "attach")], 6))
    out.append(scen("volume_sources", three, [at(0, "play"), setw(1, settings={"bg_volume": 0.3}), at(2, "init_volume"), setw(3, live_volume=0.9), at(4, "init_volume"),
                                              setw(5, live_volume=7.0), at(6, "init_volume"), setw(7, live_volume=None, settings={"bg_volume": 1.6}), at(8, "stop_with_fade", ms=500)], 15))
    out.append(scen("settings_all_missing_use_defaults", three, [at(0, "init_volume"), at(1, "play"), at(2, "attach"), at(3, "next"), at(4, "fade_out"), at(6, "play"), at(7, "stop_with_fade", ms=300)], 40,
                    drop_settings=list(BASE_SETTINGS)))
    out.append(scen("settings_missing_with_normalization_and_silence", three, [at(0, "play"), setw(1, meter=0.0), at(2, "fade_in", ms=500)], 50,
                    drop_settings=["bg_normalize_enabled", "bg_end_silence_trim_enabled", "seamless_transitions_enabled"], settings={"simple_audio_mode": False}))
    out.append(scen("live_volume_out_of_range_paths", three, [at(0, "play"), setw(1, live_volume=7.0), at(2, "enabled", v=True), at(3, "pause"), at(3.5, "play"), at(4, "ensure_audible"),
                                                              at(5, "stop_with_fade", ms=400), at(6, "play"), at(7, "pause"), at(8, "ensure_audible"), setw(9, live_volume=-2.0), at(10, "enabled", v=True),
                                                              at(11, "stop_with_fade", ms=400)], 20))
    out.append(scen("saved_volume_out_of_range_paths", three, [at(0, "play"), setw(1, settings={"bg_volume": 1.6}), at(2, "enabled", v=True), at(3, "ensure_audible"), at(4, "stop_with_fade", ms=400),
                                                               at(5, "play"), at(6, "pause"), at(7, "ensure_audible"), setw(8, settings={"bg_volume": -0.4}), at(9, "enabled", v=True), at(10, "cancel_pending", restore=True)], 14))
    out.append(scen("disabled_player", three, [at(0, "enabled", v=False), at(1, "play"), at(2, "fade_in", ms=500), at(3, "enabled", v=True), at(4, "play"), at(7, "enabled", v=False)], 12))
    out.append(scen("playlist_edit_while_playing", three, [at(0, "play"), at(2, "playlist", paths=["@c", "@a"], index=1), at(3, "next")], 40))
    # ---- scenarios added to kill mutation survivors
    out.append(scen("mtime_only_change_makes_track_eligible", three, [at(0, "play"), setw(0.2, prepare_results={"@b": ["BASS error 2: open"]}), setw(30, files={"@b": "utime"})], 100))
    out.append(scen("seamless_disabled_configured_1500", three, [at(0, "play")], 40, crossfade_ms=1500, settings={"seamless_transitions_enabled": False}))
    out.append(scen("seamless_key_missing_with_records", recs, [at(0, "play")], 100, drop_settings=["seamless_transitions_enabled"]))
    tie = {"audio_start": 0.0, "audio_end": 28.0625, "fade_start": 24.0, "fade_confidence": 0.9, "hop_seconds": 0.0625}
    out.append(scen("adaptive_tie_on_tick_grid", [track("r1", 29.0, tie), track("r2", 29.0)], [at(0.4375, "play")], 60))
    frac = {"audio_start": 0.0, "audio_end": 28.0007, "fade_start": 24.0, "fade_confidence": 0.9, "hop_seconds": 0.0625}
    out.append(scen("adaptive_fraction_on_tick_grid", [track("r1", 29.0, frac), track("r2", 29.0)], [at(0.4375, "play")], 60))
    sil = {"settings": {"bg_end_silence_trim_enabled": True}}
    out.append(scen("silence_disabled_midrun_resets", [track("a", 60.0), track("b", 60.0)], [setw(0, meter=0.0), at(0, "play"), setw(16.0, settings={"bg_end_silence_trim_enabled": False}),
                                                                                       setw(16.4, settings={"bg_end_silence_trim_enabled": True})], 40, **sil))
    out.append(scen("silence_tick_gap_2_5s", [track("a", 60.0), track("b", 60.0)], [setw(0, meter=0.0), at(0, "play")], 40, tick_skips=[[15.6, 18.1]], **sil))
    out.append(scen("silence_uses_file_duration_not_audio_end", [track("a", 100.0, {"audio_start": 0.0, "audio_end": 50.0}), track("b", 100.0)], [setw(0, meter=0.0), at(0, "play")], 60, **sil))
    short = {"audio_start": 5.0, "audio_end": 5.0, "hop_seconds": 0.05, "fade_confidence": 0.0}
    out.append(scen("next_window_shorter_than_100ms", [track("a", 20.0), track("b", 20.0, short)], [at(0, "play")], 40))
    out.append(scen("previous_falls_back_when_crossfade_refused", three, [at(0, "play"), setw(1, responses={"start_crossfade": False}), at(3, "prev"), at(6, "next")], 12))
    out.append(scen("startup_fade_keeps_allow_flag", three, [setw(0, karaoke=True), at(0, "preload_async"), at(0.01, "fade_in", ms=1000, allow=True)], 6, preload_latency_ms=100))
    out.append(scen("startup_preload_called_twice_in_flight", three, [at(0, "preload_async"), at(0.005, "preload_async")], 4, preload_latency_ms=100))
    out.append(scen("startup_stale_by_playlist_clears_resume", three, [at(0, "preload_async"), at(0.01, "play"), at(0.02, "playlist", paths=["@b", "@c"], index=0),
                                                                        at(0.2, "preload_async")], 6, preload_latency_ms=100))
    return out


EVENTS = [("play", 6), ("pause", 3), ("stop", 3), ("stop_with_fade", 3), ("next", 6), ("prev", 4), ("fade_out", 4), ("fade_in", 5), ("ensure_audible", 2),
          ("cancel_pending", 2), ("set_volume", 3), ("stop_after", 3), ("enabled", 2), ("preload", 2), ("preload_async", 2), ("init_volume", 1), ("attach", 1),
          ("set_world", 8), ("playlist", 2), ("delete_file", 1), ("touch_file", 2)]


def random_scenario(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 5)
    tracks = []
    for i in range(n):
        dur = rng.choice([4.0, 7.0, 12.0, 20.0, 31.5, 45.0])
        rec = None
        r = rng.random()
        if r < 0.6:
            end = round(dur - rng.choice([0.0, 0.3, 1.5, 4.0]), 1)
            rec = {"audio_start": rng.choice([0.0, 0.3, 1.2]), "audio_end": max(end, 1.0),
                   "fade_start": rng.choice([None, round(max(end - 6, 0.5), 1)]), "fade_confidence": rng.choice([0.0, 0.1, 0.5, 0.85, 1.0])}
            if rng.random() < 0.1:
                rec["duration"] = dur + 3
            if rng.random() < 0.1:
                rec["analysis_version"] = 2
        t = track(f"t{i}", dur, rec)
        if rng.random() < 0.5:
            t["loud"] = {"gain_linear": rng.choice([0.01, 0.4, 1.0, 2.5, 8.0, 0.0]), "gain_db": 1.0}
        if rng.random() < 0.2:
            t["prepare"] = [rng.choice(["ok", "BASS error 2: x", "flaky"]) for _ in range(rng.randint(1, 2))]
        tracks.append(t)
    settings = {
        "simple_audio_mode": rng.random() < 0.5, "bg_normalize_enabled": rng.random() < 0.8,
        "seamless_transitions_enabled": rng.random() < 0.8, "bg_end_silence_trim_enabled": rng.random() < 0.4,
        "bg_volume": rng.choice([0.0, 0.25, 0.8, 1.0]), "bgm_fade_in_floor_db": rng.choice([-60.0, -40.0, -30.5]),
    }
    run_s = rng.choice([40, 60, 90])
    script = []
    for _ in range(rng.randint(3, 22)):
        ev = rng.choices([e for e, _ in EVENTS], weights=[w for _, w in EVENTS])[0]
        t = round(rng.uniform(0, run_s - 5), 2)
        if ev == "stop_with_fade":
            script.append(at(t, ev, ms=rng.choice([0, 300, 1000])))
        elif ev == "fade_out":
            script.append(at(t, ev, ms=rng.choice([200, 1000, 3000])))
        elif ev == "fade_in":
            script.append(at(t, ev, target=rng.choice([None, 0.0, 0.5, 1.3]), ms=rng.choice([500, 3000]), allow=rng.random() < 0.5))
        elif ev == "cancel_pending":
            script.append(at(t, ev, restore=rng.random() < 0.7))
        elif ev == "set_volume":
            script.append(at(t, ev, v=rng.choice([-0.3, 0.0, 0.4, 1.0, 1.5])))
        elif ev == "stop_after":
            script.append(at(t, ev, v=rng.random() < 0.5))
        elif ev == "enabled":
            script.append(at(t, ev, v=rng.random() < 0.6))
        elif ev == "set_world":
            kind = rng.choice(["karaoke", "meter", "responses", "settings", "live", "lead", "settle"])
            if kind == "karaoke":
                script.append(setw(t, karaoke=rng.random() < 0.4))
            elif kind == "meter":
                script.append(setw(t, meter=rng.choice([0.0, 0.01, 0.3, 0.9])))
            elif kind == "responses":
                name = rng.choice(["play", "start_crossfade", "complete_crossfade", "load", "install_prepared_primary", "install_prepared_secondary"])
                v = rng.choice([False, None, None, {"raise": rng.choice(["BASS error 2: o", "other"])}])
                script.append(setw(t, responses={name: v}))
            elif kind == "settings":
                key = rng.choice(["bg_volume", "seamless_transitions_enabled", "bg_end_silence_trim_enabled", "simple_audio_mode", "bg_normalize_enabled"])
                script.append(setw(t, settings={key: rng.choice([0.0, 0.5, 0.9]) if key == "bg_volume" else rng.random() < 0.5}))
            elif kind == "live":
                script.append(setw(t, live_volume=rng.choice([None, 0.2, 0.7])))
            elif kind == "lead":
                script.append(setw(t, lead=rng.choice([0.0, 0.2, 0.5])))
            else:
                script.append(setw(t, settle_ms=rng.choice([0, 80, 250])))
        elif ev == "playlist":
            k = rng.randint(1, n)
            names = rng.sample([f"@t{i}" for i in range(n)], k)
            script.append(at(t, ev, paths=names, index=rng.randrange(k), realign=rng.random() < 0.3))
        elif ev == "delete_file":
            script.append(setw(t, files={f"@t{rng.randrange(n)}": "delete"}))
        elif ev == "touch_file":
            script.append(setw(t, files={f"@t{rng.randrange(n)}": "touch"}))
        else:
            script.append(at(t, ev))
    if rng.random() < 0.3:
        script.insert(0, at(0, "play"))
    script.sort(key=lambda e: e["at"])
    extra = {}
    if rng.random() < 0.3:
        extra["tick_skips"] = [[rng.randint(2, 30), rng.randint(31, 45)]]
    extra["lead_s"] = rng.choice([0.0, 0.0, 0.3])
    extra["preload_latency_ms"] = rng.choice([0, 40, 400, 900])
    extra["crossfade_ms"] = rng.choice([5000, 2500, 9000])
    extra["volume"] = rng.choice([0.8, 0.5])
    extra["settle_ms"] = 0
    return scen(f"random_{seed}", tracks, script, run_s, settings=settings, **extra)


def run_python(scenario):
    return sc.run(copy.deepcopy(scenario))


def rust(binary, replay):
    proc = subprocess.run([str(binary)], input=json.dumps(replay).encode(), capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-800:])
    return json.loads(proc.stdout)["results"]


def strict_eq(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def compare(name, out, results):
    exp = out["expected"]
    problems = []
    if len(exp) != len(results):
        return [f"{name}: event count python={len(exp)} rust={len(results)}"]
    for i, (e, r) in enumerate(zip(exp, results)):
        ev = out["replay"]["events"][i]
        if str(e["ret"]).startswith("EXC:"):
            problems.append(f"{name}: python raised {e['ret']} at event {i} ({ev['ev']} {ev['a']}) - not comparable")
            break
        if not strict_eq(e["cmds"], r["cmds"]):
            problems.append(f"{name}: event {i} t+{e['t'] - sc.T0_MS}ms {ev['ev']} {ev['a']}\n   python cmds: {json.dumps(e['cmds'])[:900]}\n   rust   cmds: {json.dumps(r['cmds'])[:900]}")
            break
        if not strict_eq(e["ret"], r["ret"]):
            problems.append(f"{name}: event {i} {ev['ev']} return python={e['ret']!r} rust={r['ret']!r}")
            break
        if not strict_eq(e["state"], r["state"]):
            diff = {k: (e["state"][k], r["state"].get(k)) for k in e["state"] if e["state"][k] != r["state"].get(k) or type(e["state"][k]) != type(r["state"].get(k))}
            problems.append(f"{name}: event {i} t+{e['t'] - sc.T0_MS}ms {ev['ev']} {ev['a']} state differs {diff}")
            break
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--binary", default=str(DEFAULT_BIN))
    ap.add_argument("--only", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    all_scen = scripted() + [random_scenario(s) for s in range(args.seeds)]
    if args.only:
        all_scen = [s for s in all_scen if args.only in s["name"]]
    problems, events, cmds, raised = [], 0, 0, 0
    kinds = {}
    for s in all_scen:
        out = run_python(s)
        exc = [e for e in out["expected"] if str(e["ret"]).startswith("EXC:")]
        if exc:
            raised += 1
        res = rust(args.binary, out["replay"])
        problems += compare(s["name"], out, res)
        events += len(out["expected"])
        for e in out["expected"]:
            for c in e["cmds"]:
                cmds += 1
                kinds[c["c"]] = kinds.get(c["c"], 0) + 1
    print(f"scenarios={len(all_scen)} events={events} commands={cmds} python_raised_in={raised}")
    if not args.quiet:
        print("command kinds seen:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    if problems:
        print(f"DIFFERENT: {len(problems)}")
        for p in problems[:12]:
            print(" -", p)
        return 1
    print("IDENTICAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
