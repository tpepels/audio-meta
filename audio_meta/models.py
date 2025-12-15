from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


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
    extra: Dict[str, str] = field(default_factory=dict)
    match_confidence: Optional[float] = None

    def to_record(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "fingerprint": self.fingerprint,
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
            "extra": {key: self._serialize(value) for key, value in self.extra.items()},
            "match_confidence": self.match_confidence,
        }

    @staticmethod
    def _serialize(value: object) -> object:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, list):
            return [TrackMetadata._serialize(item) for item in value]
        if isinstance(value, dict):
            return {k: TrackMetadata._serialize(v) for k, v in value.items()}
        return value


class ProcessingError(Exception):
    """Raised when a file cannot be processed but the daemon should keep running."""
