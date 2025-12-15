from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import musicbrainzngs
import acoustid

from ..config import ProviderSettings
from ..models import TrackMetadata

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LookupResult:
    track: TrackMetadata
    score: float


class MusicBrainzClient:
    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings
        musicbrainzngs.set_useragent(
            "audio-meta",
            "0.1",
            contact=settings.musicbrainz_useragent,
        )

    def enrich(self, meta: TrackMetadata) -> Optional[LookupResult]:
        fingerprint, duration = self._fingerprint(meta)
        if not fingerprint:
            return None
        meta.fingerprint = fingerprint
        meta.duration_seconds = duration
        acoustic_matches = acoustid.lookup(
            self.settings.acoustid_api_key,
            fingerprint,
            duration,
        )
        for score, recording_id, title, artist in self._iter_acoustid(acoustic_matches):
            try:
                recording = musicbrainzngs.get_recording_by_id(
                    recording_id,
                    includes=["artists", "releases", "work-rels", "artist-credits"],
                )["recording"]
            except musicbrainzngs.ResponseError as exc:
                logger.warning("MusicBrainz error for %s: %s", meta.path, exc)
                continue
            self._apply_recording(meta, recording, title, artist)
            meta.acoustid_id = recording_id
            return LookupResult(meta, score=score)
        return None

    def _fingerprint(self, meta: TrackMetadata) -> tuple[Optional[str], Optional[int]]:
        try:
            fingerprint, duration = acoustid.fingerprint_file(str(meta.path))
            return fingerprint, duration
        except acoustid.FingerprintGenerationError as exc:
            logger.error("Fingerprint failed for %s: %s", meta.path, exc)
            return None, None

    def _iter_acoustid(self, response):
        for match in response.get("results", []):
            score = float(match.get("score", 0))
            for recording in match.get("recordings", []):
                yield score, recording["id"], recording.get("title"), recording["artists"][0]["name"]

    def _apply_recording(self, meta: TrackMetadata, recording: dict, title: Optional[str], artist: Optional[str]) -> None:
        release_list = recording.get("releases", [])
        release = release_list[0] if release_list else {}
        meta.title = title or recording.get("title")
        meta.artist = artist or self._first_artist(recording)
        meta.album = release.get("title")
        meta.album_artist = self._first_artist(release) or meta.artist
        work_rels = recording.get("work-relation-list", [])
        if work_rels:
            work = work_rels[0].get("work", {})
            meta.work = work.get("title")
            meta.composer = self._first_artist(work)
        self._capture_performers(recording, meta)

    def _first_artist(self, entity: dict) -> Optional[str]:
        credits = entity.get("artist-credit", [])
        if credits:
            names = [c["name"] for c in credits if isinstance(c, dict) and "name" in c]
            return ", ".join(names) if names else None
        artists = entity.get("artist-list", [])
        if artists:
            return artists[0].get("name")
        return None

    def _capture_performers(self, recording: dict, meta: TrackMetadata) -> None:
        relations = recording.get("artist-relation-list", [])
        for rel in relations:
            role = rel.get("type", "").lower()
            name = rel.get("artist", {}).get("name")
            if not name:
                continue
            if role in {"conductor"}:
                meta.conductor = name
            elif role in {"performer", "instrumentalist", "orchestra"}:
                meta.performers.append(name)
