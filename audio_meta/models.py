from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .meta_keys import DISCNUMBER, MATCH_SOURCE, TRACKNUMBER, TRACK_TOTAL


@dataclass(slots=True)
class TrackMetadata:
    path: Path
    fingerprint: Optional[str] = None
    acoustid_id: Optional[str] = None
    musicbrainz_track_id: Optional[str] = None
    musicbrainz_release_id: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    artist: Optional[str] = None
    album_artist: Optional[str] = None
    composer: Optional[str] = None
    performers: List[str] = field(default_factory=list)
    conductor: Optional[str] = None
    work: Optional[str] = None
    movement: Optional[str] = None
    genre: Optional[str] = None
    duration_seconds: Optional[int] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    track_total: Optional[int] = None
    match_source: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    match_confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.extra:
            return
        if self.track_number is None and TRACKNUMBER in self.extra:
            self.track_number = _parse_int(self.extra.get(TRACKNUMBER))
            self.extra.pop(TRACKNUMBER, None)
        if self.disc_number is None and DISCNUMBER in self.extra:
            self.disc_number = _parse_int(self.extra.get(DISCNUMBER))
            self.extra.pop(DISCNUMBER, None)
        if self.track_total is None and TRACK_TOTAL in self.extra:
            self.track_total = _parse_int(self.extra.get(TRACK_TOTAL))
            self.extra.pop(TRACK_TOTAL, None)
        if self.match_source is None and MATCH_SOURCE in self.extra:
            value = self.extra.get(MATCH_SOURCE)
            self.match_source = value if isinstance(value, str) else str(value)
            self.extra.pop(MATCH_SOURCE, None)

    def to_record(self) -> Dict[str, object]:
        payload = {
            "path": str(self.path),
            "fingerprint": "<omitted>" if self.fingerprint else None,
            "acoustid_id": self.acoustid_id,
            "musicbrainz_track_id": self.musicbrainz_track_id,
            "musicbrainz_release_id": self.musicbrainz_release_id,
            "title": self._serialize(self.title),
            "album": self._serialize(self.album),
            "artist": self._serialize(self.artist),
            "album_artist": self._serialize(self.album_artist),
            "composer": self._serialize(self.composer),
            "performers": [self._serialize(name) for name in self.performers],
            "conductor": self._serialize(self.conductor),
            "work": self._serialize(self.work),
            "movement": self._serialize(self.movement),
            "genre": self._serialize(self.genre),
            "duration_seconds": self.duration_seconds,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "track_total": self.track_total,
            "match_source": self._serialize(self.match_source),
            "extra": {key: self._serialize(value) for key, value in self.extra.items()},
            "match_confidence": self.match_confidence,
        }
        return {key: self._serialize(value) for key, value in payload.items()}

    @staticmethod
    def _serialize(value: object) -> object:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (list, tuple)):
            return [TrackMetadata._serialize(item) for item in value]
        if isinstance(value, dict):
            return {
                (
                    k.decode("utf-8", errors="replace")
                    if isinstance(k, bytes)
                    else str(k)
                    if isinstance(k, Path)
                    else k
                ): TrackMetadata._serialize(v)
                for k, v in value.items()
            }
        return value


class ProcessingError(Exception):
    """Raised when a file cannot be processed but the daemon should keep running."""


def _parse_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if "/" in cleaned:
            cleaned = cleaned.split("/", 1)[0].strip()
        if cleaned.isdigit():
            return int(cleaned)
        return None
    try:
        as_str = str(value).strip()
    except Exception:
        return None
    if as_str.isdigit():
        return int(as_str)
    return None
