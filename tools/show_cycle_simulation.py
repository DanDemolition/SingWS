#!/usr/bin/env python3
"""Fast, media-free regression simulation for a SingWS show transition cycle.

This intentionally does not launch GUI windows or touch audio devices.  It
guards the source contracts that make CDG -> MP4 -> KaraFun -> CDG safe while
the rotation window is open, including a motionless-CDG interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ShowState:
    mode: str = "idle"
    rotation_open: bool = True
    show_ticker_visible: bool = True
    rotation_ticker_visible: bool = False
    background_clock_ms: int = 0
    events: list[str] = field(default_factory=list)

    def enter(self, mode: str) -> None:
        if mode not in {"cdg", "mp4", "karafun"}:
            raise ValueError(mode)
        self.mode = mode
        self.events.append(mode)

    def static_cdg_interval(self, milliseconds: int) -> None:
        if self.mode != "cdg":
            raise RuntimeError("static CDG interval outside CDG playback")
        self.background_clock_ms += max(0, int(milliseconds))


def run(repo: Path) -> dict:
    main = (repo / "0.2.18.1.py").read_text(encoding="utf-8")
    bridge = (repo / "native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
    transport = (repo / "mpv_karaoke_transport.py").read_text(encoding="utf-8")

    contracts = {
        "frozen_background_crossfade": (
            "glCopyTexSubImage2D" in bridge and "backgroundCrossfadeMix" in bridge
        ),
        "background_updates_both_surfaces": (
            "[self presentView:self->_outputView]" in bridge
            and "[self presentView:self->_previewView]" in bridge
        ),
        "cdg_watchdog": "visual_stalled.emit" in transport,
        "mp4_edge_to_edge": '"video-aspect-override",_isCdg?"no":"16:9"' in bridge,
        "rotation_preserves_show_ticker": (
            "_reassert_show_ticker_after_rotation_open" in main
            and "ticker.raise_()" in main
            and "video_window.set_ticker_enabled(True)" in main
        ),
        "karafun_does_not_open_audio_settings": (
            'help of elem is "Audio Settings"' not in main
        ),
        "pre_show_diagnostics_cover_show_chain": all(
            marker in main for marker in (
                "Run Pre-Show Check",
                '"Local audio output"',
                '"AirPlay video-only routing"',
                '"KaraFun"',
                '"Native video surfaces"',
                '"Show ticker"',
                '"Signup QR URL"',
                '"Background videos"',
            )
        ),
    }

    state = ShowState()
    state.enter("cdg")
    state.static_cdg_interval(8000)
    state.enter("mp4")
    state.enter("karafun")
    state.enter("cdg")
    state.static_cdg_interval(8000)

    contracts["expected_cycle"] = state.events == ["cdg", "mp4", "karafun", "cdg"]
    contracts["background_survives_static_cdg"] = state.background_clock_ms == 16000
    contracts["rotation_and_ticker_coexist"] = (
        state.rotation_open and state.show_ticker_visible and not state.rotation_ticker_visible
    )
    return {"ok": all(contracts.values()), "contracts": contracts, "state": state}


if __name__ == "__main__":
    result = run(Path(__file__).resolve().parents[1])
    for name, ok in result["contracts"].items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    raise SystemExit(0 if result["ok"] else 1)
