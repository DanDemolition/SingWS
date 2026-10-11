"""Formatting for the once-a-minute [AUDIO-DIAG] log line (engine independent)."""


def format_audio_diag(snapshot) -> str:
    """One log line from the engine's audio_diagnostics_snapshot(); empty when there is nothing to say."""
    if not snapshot:
        return ""
    m = snapshot.get("master") or {}
    stalled = int(snapshot.get("mixer_stalled", 0) or 0)
    if not m.get("blocks") and not stalled:
        return ""
    p99 = m.get("p99_upper_us")
    p99_text = f"<={p99:.0f}us" if p99 is not None else ">25000us"
    cpu = snapshot.get("bass_cpu")
    cpu_text = f"{cpu:.1f}%" if isinstance(cpu, (int, float)) else "n/a"
    return (
        f"[AUDIO-DIAG] master-dsp[{snapshot.get('engine', 'python')}] blocks={m.get('blocks', 0)} mean={m.get('mean_us', 0):.0f}us "
        f"p99{p99_text} max={m.get('max_us', 0):.0f}us over_budget={m.get('over_budget', 0)} "
        f"gap_max={m.get('gap_max_ms', 0):.0f}ms gaps>100ms={m.get('gaps_over_100ms', 0)} "
        f"gaps>500ms={m.get('gaps_over_500ms', 0)} restarts={m.get('restarts', 0)} | "
        f"bass_cpu={cpu_text} mixer_stalled_polls={stalled}"
    )
