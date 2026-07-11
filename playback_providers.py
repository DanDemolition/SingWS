"""Playback provider primitives for SingWS.

This module is intentionally UI-free. Providers describe how a song reference
can be authenticated, prepared, and played without assuming every song is a
local media file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol


class SongProvider(str, Enum):
    LOCAL = "local"
    KARAFUN_LOCAL = "karafun_local"
    KARAFUN_STREAMING = "karafun_streaming"
    EXTERNAL_KARAFUN = "external_karafun"


class AuthorizationRequirement(str, Enum):
    NONE = "none"
    KARAFUN_PRO_SUBSCRIPTION = "karafun_pro_subscription"
    KARAFUN_BUSINESS_SUBSCRIPTION = "karafun_business_subscription"
    OFFICIAL_KARAFUN_APP = "official_karafun_app"
    KARAFUN_PARTNER_AGREEMENT = "karafun_partner_agreement"


class AvailabilityStatus(str, Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    EXTERNALLY_CONTROLLED = "externally_controlled"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ProviderTrackRef:
    provider: SongProvider = SongProvider.LOCAL
    provider_track_id: str = ""
    provider_url: str = ""
    local_reference_path: str = ""
    authorization_requirement: AuthorizationRequirement = AuthorizationRequirement.NONE
    availability_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    title: str = ""
    artist: str = ""
    duration_secs: Optional[int] = None
    language: str = ""
    duet: bool = False
    explicit: Optional[bool] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_local_media(self) -> bool:
        return self.provider == SongProvider.LOCAL

    def to_track_dict(self) -> Dict[str, Any]:
        display = " - ".join(p for p in (self.artist, self.title) if p)
        data: Dict[str, Any] = {
            "provider": self.provider.value,
            "provider_track_id": self.provider_track_id,
            "provider_url": self.provider_url,
            "local_reference_path": self.local_reference_path,
            "authorization_requirement": self.authorization_requirement.value,
            "availability_status": self.availability_status.value,
            "artist": self.artist,
            "title": self.title,
            "duration_secs": self.duration_secs,
            "language": self.language,
            "duet": self.duet,
            "explicit": self.explicit,
            "display": display,
        }
        data.update(self.extra)
        if self.provider == SongProvider.LOCAL:
            data["path"] = self.local_reference_path
        else:
            data["path"] = provider_reference_path(
                self.provider,
                self.provider_track_id or self.provider_url or self.local_reference_path,
            )
        return {k: v for k, v in data.items() if v not in (None, "")}


def provider_reference_path(provider: SongProvider | str, identifier: str) -> str:
    provider_value = provider.value if isinstance(provider, SongProvider) else str(provider or "")
    ident = str(identifier or "").strip()
    if not provider_value or provider_value == SongProvider.LOCAL.value:
        return ident
    return f"{provider_value}:{ident}"


def provider_identity(track: Dict[str, Any]) -> tuple[str, str, str, str]:
    provider = str(track.get("provider") or SongProvider.LOCAL.value).strip().lower()
    provider_track_id = str(track.get("provider_track_id") or "").strip().lower()
    provider_url = str(track.get("provider_url") or "").strip().lower()
    local_reference_path = str(track.get("local_reference_path") or track.get("path") or "").strip().lower()
    return provider, provider_track_id, provider_url, local_reference_path


class PlaybackProvider(Protocol):
    provider: SongProvider

    def authenticate(self) -> bool: ...
    def sign_out(self) -> None: ...
    def get_account_status(self) -> Dict[str, Any]: ...
    def search_tracks(self, query: str, *, limit: int = 25) -> List[ProviderTrackRef]: ...
    def get_track(self, provider_track_id: str) -> Optional[ProviderTrackRef]: ...
    def check_availability(self, track: ProviderTrackRef) -> AvailabilityStatus: ...
    def prepare(self, track: ProviderTrackRef) -> bool: ...
    def play(self, track: ProviderTrackRef) -> bool: ...
    def pause(self) -> bool: ...
    def resume(self) -> bool: ...
    def stop(self) -> bool: ...
    def seek(self, seconds: float) -> bool: ...
    def set_key(self, semitones: int) -> bool: ...
    def set_tempo(self, ratio: float) -> bool: ...
    def get_playback_state(self) -> Dict[str, Any]: ...
    def get_duration(self) -> Optional[float]: ...
    def get_position(self) -> Optional[float]: ...
    def cleanup(self) -> None: ...
