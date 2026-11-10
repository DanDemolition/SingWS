"""KaraFun's "Top US" chart -> the venue's request page.

KaraFun serves its chart only to a real browser, so this server-less side reads it with the same windowless Chrome helper the web player uses (no sign-in
needed) and posts the 100 songs to the venue's own server (`/api/v1/top100_sync.php`, venue API key). The request page shows the list only when the venue
switched "Top 100" on in the dashboard. Runs at most once a day; nothing here touches playback.
"""
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

CHART_ID = 7                 # KaraFun's Discover > Top > "Top US"
EVERY_SECONDS = 20 * 3600
READ_TIMEOUT_S = 150


def _home() -> Path:
    home = os.environ.get("SINGWS_HOME")
    return Path(home) if home else Path.home() / "SingWS"


def state_path() -> Path:
    return _home() / "karafun_top100_state.json"


def last_success(path: Optional[Path] = None) -> float:
    try:
        return float(json.loads((path or state_path()).read_text(encoding="utf-8")).get("last_success", 0.0))
    except Exception:
        return 0.0


def is_due(now: Optional[float] = None, *, every: float = EVERY_SECONDS, path: Optional[Path] = None) -> bool:
    return (now if now is not None else time.time()) - last_success(path) >= every


def _remember(path: Optional[Path], when: float) -> None:
    p = path or state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_success": when}), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def read_chart(chart_id: int = CHART_ID, *, make_web: Optional[Callable] = None, find_browser: Optional[Callable] = None,
               profile_dir=None, timeout: float = READ_TIMEOUT_S, on_log: Callable[[str], None] = lambda s: None) -> List[dict]:
    """The chart's songs in rank order ([{title, artist, seconds}]); raises RuntimeError with a plain reason when it cannot be read."""
    import karafun_web
    box: dict = {}
    done = threading.Event()

    def on_message(message: dict) -> None:
        if message.get("type") == "top":
            box["songs"] = message.get("songs") or []
            box["total"] = message.get("total")
            done.set()
        elif message.get("type") == "error":
            text = str(message.get("message") or "KaraFun's player reported an error")
            if "still starting" in text or "songs" in box or "error" in box:
                return                      # not ready yet (asked again below), or the chart/first error is already in and this is only Chrome closing
            box["error"] = text
            done.set()

    browser = (find_browser or karafun_web.ensure_browser)(lambda done_bytes, total: None)
    profile = Path(profile_dir) if profile_dir else _home() / "Chrome-KaraFun-Top"
    web = (make_web or karafun_web.KaraFunWeb)(profile, on_message=on_message, on_log=on_log, chrome_path=browser)
    try:
        web.start()
        started = time.monotonic()
        last_asked = -100.0
        while not done.is_set() and time.monotonic() - started < timeout:
            if time.monotonic() - last_asked >= 4.0:        # until the page answers: the first asks come before its player objects exist
                web.top(chart_id)
                last_asked = time.monotonic()
            done.wait(0.5)
    finally:
        try:
            web.close()
        except Exception:
            pass
    songs = box.get("songs")
    if not songs and "error" in box:
        raise RuntimeError(box["error"])
    if not songs:
        raise RuntimeError("KaraFun's chart could not be read in time")
    return songs


def upload(config: dict, songs: List[dict], *, source: str = "KaraFun Top US", post: Optional[Callable] = None) -> Tuple[bool, str]:
    """Send the chart to the venue's server. `config` = {base, user, key}. Never raises; returns (ok, reason)."""
    if not (config.get("base") and config.get("user") and config.get("key")):
        return False, "not connected to the SingWS server (Settings > Network)"
    if post is None:
        import requests
        post = requests.post
    try:
        response = post(f"{config['base']}/api/v1/top100_sync.php", params={"user": config["user"]},
                        data=json.dumps({"source": source, "songs": songs}),
                        headers={"X-API-Key": config["key"], "Content-Type": "application/json", "Accept": "application/json"}, timeout=30)
        if response.status_code == 200 and (response.json() or {}).get("ok"):
            return True, f"{len(songs)} songs sent"
        try:
            reason = (response.json() or {}).get("error") or f"HTTP {response.status_code}"
        except Exception:
            reason = f"HTTP {response.status_code}"
        return False, str(reason)
    except Exception as error:
        return False, f"{type(error).__name__}"


def refresh(config: dict, *, force: bool = False, now: Optional[float] = None, path: Optional[Path] = None,
            on_log: Callable[[str], None] = lambda s: None, reader: Optional[Callable] = None, sender: Optional[Callable] = None) -> Tuple[bool, str]:
    """Read the chart and send it, at most once per EVERY_SECONDS unless forced. (ok, message). Run it on a background thread."""
    when = now if now is not None else time.time()
    if not force and not is_due(when, path=path):
        return True, "not due"
    try:
        songs = (reader or read_chart)(on_log=on_log) if reader is None else reader()
    except Exception as error:
        return False, f"could not read the chart: {error}"
    ok, reason = (sender or upload)(config, songs)
    if ok:
        _remember(path, when)
    return ok, reason
