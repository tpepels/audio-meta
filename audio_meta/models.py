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

    def to_record(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "fingerprint": self.fingerprint,
            "acoustid_id": self.acoustid_id,
            "musicbrainz_track_id": self.musicbrainz_track_id,
            "title": self.title,
            "album": self.album,
            "artist": self.artist,
            "album_artist": self.album_artist,
            "composer": self.composer,
            "performers": list(self.performers),
            "conductor": self.conductor,
            "work": self.work,
            "movement": self.movement,
            "genre": self.genre,
            "duration_seconds": self.duration_seconds,
            "extra": dict(self.extra),
        }


class ProcessingError(Exception):
    """Raised when a file cannot be processed but the daemon should keep running."""
