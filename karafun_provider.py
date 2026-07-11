"""Safe KaraFun provider integration scaffolding.

No authentication scraping, private API calls, media extraction, decryption, or
stream downloading happens here. Until KaraFun grants an official third-party
playback/control integration, SingWS can only store references and hand them to
the host for use in KaraFun's own software.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from playback_providers import (
    AuthorizationRequirement,
    AvailabilityStatus,
    PlaybackProvider,
    ProviderTrackRef,
    SongProvider,
)


OFFICIAL_INTEGRATION_FINDINGS = {
    "safe_mode": "assisted_external",
    "public_api": False,
    "embeddable_player": False,
    "sdk": False,
    "oauth": False,
    "notes": (
        "Public KaraFun pages describe official KaraFun applications, Pro and "
        "Business subscriptions, offline catalog features inside KaraFun, and "
        "contact channels. They do not publish a third-party playback API, SDK, "
        "OAuth flow, embeddable player, or documented .kfn decoder for SingWS."
    ),
}


@dataclass(frozen=True)
class KaraFunReference:
    title: str
    artist: str = ""
    provider_track_id: str = ""
    provider_url: str = ""
    local_reference_path: str = ""
    streaming: bool = True

    def to_provider_track(self) -> ProviderTrackRef:
        provider = SongProvider.KARAFUN_STREAMING if self.streaming else SongProvider.KARAFUN_LOCAL
        requirement = (
            AuthorizationRequirement.KARAFUN_PRO_SUBSCRIPTION
            if self.streaming
            else AuthorizationRequirement.OFFICIAL_KARAFUN_APP
        )
        return ProviderTrackRef(
            provider=provider,
            provider_track_id=str(self.provider_track_id or "").strip(),
            provider_url=str(self.provider_url or "").strip(),
            local_reference_path=str(self.local_reference_path or "").strip(),
            authorization_requirement=requirement,
            availability_status=AvailabilityStatus.EXTERNALLY_CONTROLLED,
            title=str(self.title or "").strip(),
            artist=str(self.artist or "").strip(),
        )


class AssistedKaraFunProvider(PlaybackProvider):
    provider = SongProvider.EXTERNAL_KARAFUN

    def __init__(self, *, auto_open: bool = False):
        self.auto_open = bool(auto_open)
        self._active: Optional[ProviderTrackRef] = None

    def authenticate(self) -> bool:
        return False

    def sign_out(self) -> None:
        self._active = None

    def get_account_status(self) -> Dict[str, Any]:
        return {
            "authenticated": False,
            "subscription_verified": False,
            "mode": "assisted_external",
            "reason": "No public official KaraFun third-party auth/playback API is configured.",
        }

    def search_tracks(self, query: str, *, limit: int = 25) -> List[ProviderTrackRef]:
        query = str(query or "").strip()
        if not query:
            return []
        # Assisted mode stores the host's search term only. Catalog results must
        # come from an official KaraFun API or manual host selection.
        return [
            ProviderTrackRef(
                provider=SongProvider.EXTERNAL_KARAFUN,
                provider_track_id=query,
                authorization_requirement=AuthorizationRequirement.OFFICIAL_KARAFUN_APP,
                availability_status=AvailabilityStatus.EXTERNALLY_CONTROLLED,
                title=query,
            )
        ][: max(0, int(limit or 0))]

    def get_track(self, provider_track_id: str) -> Optional[ProviderTrackRef]:
        text = str(provider_track_id or "").strip()
        if not text:
            return None
        return self.search_tracks(text, limit=1)[0]

    def check_availability(self, track: ProviderTrackRef) -> AvailabilityStatus:
        return AvailabilityStatus.EXTERNALLY_CONTROLLED

    def prepare(self, track: ProviderTrackRef) -> bool:
        self._active = track
        return True

    def play(self, track: ProviderTrackRef) -> bool:
        self._active = track
        if self.auto_open:
            return self.open_in_karafun(track)
        return False

    def open_in_karafun(self, track: ProviderTrackRef) -> bool:
        target = track.provider_url or track.local_reference_path
        if not target:
            return False
        try:
            if os.name == "posix":
                subprocess.Popen(["open", target])
                return True
        except Exception:
            return False
        return False

    def pause(self) -> bool:
        return False

    def resume(self) -> bool:
        return False

    def stop(self) -> bool:
        self._active = None
        return False

    def seek(self, seconds: float) -> bool:
        return False

    def set_key(self, semitones: int) -> bool:
        return False

    def set_tempo(self, ratio: float) -> bool:
        return False

    def get_playback_state(self) -> Dict[str, Any]:
        return {
            "provider": self.provider.value,
            "state": "externally_controlled" if self._active else "idle",
            "completion_confirmed": False,
        }

    def get_duration(self) -> Optional[float]:
        return None

    def get_position(self) -> Optional[float]:
        return None

    def cleanup(self) -> None:
        self._active = None


def kfn_reference(path: str, *, title: str = "", artist: str = "") -> ProviderTrackRef:
    path = str(path or "").strip()
    if not path.lower().endswith(".kfn"):
        raise ValueError("KaraFun local references must use a .kfn path.")
    name = os.path.splitext(os.path.basename(path))[0]
    return ProviderTrackRef(
        provider=SongProvider.KARAFUN_LOCAL,
        local_reference_path=path,
        authorization_requirement=AuthorizationRequirement.OFFICIAL_KARAFUN_APP,
        availability_status=AvailabilityStatus.EXTERNALLY_CONTROLLED,
        title=str(title or name).strip(),
        artist=str(artist or "").strip(),
    )
