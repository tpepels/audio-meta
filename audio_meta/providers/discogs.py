from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import re
from pathlib import Path
from typing import Dict, List, Optional

from ..config import ProviderSettings
from ..heuristics import PathGuess, guess_metadata_from_path
from ..models import TrackMetadata
from .musicbrainz import LookupResult
from ..cache import MetadataCache

logger = logging.getLogger(__name__)


class DiscogsClient:
    def __init__(self, settings: ProviderSettings, cache: Optional[MetadataCache] = None) -> None:
        if not settings.discogs_token:
            raise ValueError("Discogs token required")
        self.token = settings.discogs_token
        self.useragent = settings.discogs_useragent
        self.cache = cache

    def supplement(self, meta: TrackMetadata) -> Optional[LookupResult]:
        """Fill missing fields without overwriting existing values."""
        return self._enrich(meta, allow_overwrite=False)

    def enrich(self, meta: TrackMetadata) -> Optional[LookupResult]:
        """Full enrichment (used as fallback when MusicBrainz fails)."""
        return self._enrich(meta, allow_overwrite=True)

    def _enrich(self, meta: TrackMetadata, allow_overwrite: bool) -> Optional[LookupResult]:
        guess = guess_metadata_from_path(meta.path)
        tags = self._read_basic_tags(meta.path)
        artist = tags.get("artist") or guess.artist
        title = tags.get("title") or guess.title
        album = tags.get("album") or guess.album
        track_number = guess.track_number
        duration = meta.duration_seconds or self._probe_duration(meta.path)
        if not (album or title):
            return None
        release = self._search_release(artist=artist, album=album, title=title)
        if not release:
            return None
        details = self._fetch_release(release["id"])
        if not details:
            return None
        track = self._match_track(details.get("tracklist", []), title, track_number, duration)
        self._apply_release(meta, details, track, allow_overwrite)
        meta.extra.setdefault("DISCOGS_RELEASE_ID", str(details.get("id")))
        score = 0.35 if allow_overwrite else 0.3
        meta.match_confidence = max(meta.match_confidence or 0.0, score)
        return LookupResult(meta, score=score)

    def _search_release(self, artist: Optional[str], album: Optional[str], title: Optional[str]) -> Optional[dict]:
        params = {
            "token": self.token,
            "type": "release",
            "per_page": 5,
        }
        if artist:
            params["artist"] = artist
        if album:
            params["release_title"] = album
        if title:
            params["track"] = title
        url = f"https://api.discogs.com/database/search?{urllib.parse.urlencode(params)}"
        cache_key = self._search_cache_key(artist, album, title)
        cached = self.cache.get_discogs_search(cache_key) if self.cache else None
        if cached is not None:
            logger.debug("Discogs cache hit for search %s", cache_key)
            return cached or None
        data = self._request(url)
        if not data:
            if self.cache:
                self.cache.set_discogs_search(cache_key, None)
            return None
        results = data.get("results", [])
        best = results[0] if results else None
        if self.cache:
            logger.debug("Discogs cache miss; storing search %s", cache_key)
            self.cache.set_discogs_search(cache_key, best)
        return best

    def _fetch_release(self, release_id: int) -> Optional[dict]:
        if self.cache:
            cached = self.cache.get_discogs_release(release_id)
            if cached:
                logger.debug("Discogs cache hit for release %s", release_id)
                return cached
        url = f"https://api.discogs.com/releases/{release_id}?token={self.token}"
        data = self._request(url)
        if data and self.cache:
            logger.debug("Discogs cache miss; storing release %s", release_id)
            self.cache.set_discogs_release(release_id, data)
        return data

    def _match_track(
        self,
        tracklist: List[dict],
        title: Optional[str],
        track_number: Optional[int],
        duration: Optional[int],
    ) -> Optional[dict]:
        def normalize(value: Optional[str]) -> Optional[str]:
            return value.lower().strip() if isinstance(value, str) else None

        norm_title = normalize(title)
        for track in tracklist:
            if norm_title and normalize(track.get("title")) == norm_title:
                return track
        if track_number:
            for track in tracklist:
                if self._parse_track_number(track.get("position")) == track_number:
                    return track
        if duration:
            for track in tracklist:
                if self._parse_duration(track.get("duration")) == duration:
                    return track
        return tracklist[0] if tracklist else None

    def _apply_release(self, meta: TrackMetadata, release: dict, track: Optional[dict], allow_overwrite: bool) -> None:
        def set_field(attr: str, value: Optional[str]) -> None:
            if not value:
                return
            current = getattr(meta, attr)
            if allow_overwrite or not current:
                setattr(meta, attr, value)

        album_artist = self._join_artists(release.get("artists", []))
        set_field("album", release.get("title"))
        set_field("album_artist", album_artist)
        set_field("artist", self._join_artists(track.get("artists", [])) if track else album_artist)
        if track and allow_overwrite:
            set_field("title", track.get("title"))
        genres = release.get("genres") or release.get("styles") or []
        set_field("genre", genres[0] if genres else None)

    def _join_artists(self, artists: List[dict]) -> Optional[str]:
        names = [artist.get("name") for artist in artists if artist.get("name")]
        return self._normalize_artist_string(", ".join(names))

    def _normalize_artist_string(self, value: str) -> Optional[str]:
        if not value:
            return None
        cleaned = []
        for chunk in re.split(r"[;,]+", value):
            base = chunk.split(" (")[0].strip()
            if base:
                cleaned.append(base)
        unique = []
        for entry in cleaned:
            if entry not in unique:
                unique.append(entry)
        return ", ".join(unique) if unique else None

    def _request(self, url: str) -> Optional[dict]:
        req = urllib.request.Request(url, headers={"User-Agent": self.useragent})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            logger.debug("Discogs HTTP error %s for %s: %s", exc.code, url, exc)
        except urllib.error.URLError as exc:
            logger.warning("Discogs request failed for %s: %s", url, exc)
        return None

    def _read_basic_tags(self, path: Path) -> Dict[str, Optional[str]]:
        from mutagen import File

        try:
            audio = File(path, easy=True)
        except Exception as exc:  # pragma: no cover
            logger.debug("Discogs tag read failed for %s: %s", path, exc)
            return {}
        if not audio or not audio.tags:
            return {}
        return {
            "artist": self._first_tag(audio, ["artist", "albumartist"]),
            "title": self._first_tag(audio, ["title"]),
            "album": self._first_tag(audio, ["album"]),
        }

    @staticmethod
    def _first_tag(audio, keys: List[str]) -> Optional[str]:
        for key in keys:
            values = audio.tags.get(key)
            if values:
                if isinstance(values, list):
                    return values[0]
                return values
        return None

    def _parse_track_number(self, position: Optional[str]) -> Optional[int]:
        if not position:
            return None
        digits = "".join(ch for ch in position if ch.isdigit())
        return int(digits) if digits else None

    def _parse_duration(self, value: Optional[str]) -> Optional[int]:
        if not value or ":" not in value:
            return None
        try:
            minutes, seconds = value.split(":", 1)
            return int(minutes) * 60 + int(seconds)
        except ValueError:
            return None

    def _probe_duration(self, path: Path) -> Optional[int]:
        try:
            from mutagen import File

            audio = File(path)
        except Exception:
            return None
        if not audio or not getattr(audio, "info", None):
            return None
        length = getattr(audio.info, "length", None)
        return int(length) if length else None

    def _search_cache_key(
        self,
        artist: Optional[str],
        album: Optional[str],
        title: Optional[str],
    ) -> str:
        def normalize(value: Optional[str]) -> str:
            return (value or "").strip().lower()

        return "|".join([normalize(artist), normalize(album), normalize(title)])
