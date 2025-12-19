from __future__ import annotations

import logging
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import difflib
import socket
import time
import urllib.error

try:  # Optional at runtime; matching can still work without AcoustID.
    import acoustid
except ModuleNotFoundError:  # pragma: no cover
    acoustid = None
try:
    import musicbrainzngs
except ModuleNotFoundError:  # pragma: no cover
    musicbrainzngs = None
try:
    from mutagen import File as MutagenFile
except ModuleNotFoundError:  # pragma: no cover
    MutagenFile = None

from ..config import ProviderSettings
from ..heuristics import PathGuess, guess_metadata_from_path
from ..models import TrackMetadata
from ..cache import MetadataCache

# Import LookupResult from the new musicbrainz package to avoid duplication
# Since we now have both musicbrainz.py (this file) and musicbrainz/ (package),
# Python prioritizes the package, so we import from there
from .musicbrainz import LookupResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReleaseTrack:
    recording_id: str
    disc_number: Optional[int]
    number: Optional[int]
    title: Optional[str]
    duration_seconds: Optional[int]


class ReleaseData:
    def __init__(
        self,
        release_id: str,
        album_title: Optional[str],
        album_artist: Optional[str],
        release_date: Optional[str],
    ) -> None:
        self.release_id = release_id
        self.album_title = album_title
        self.album_artist = album_artist
        self.release_date = release_date
        self.disc_count = 0
        self.formats: List[str] = []
        self.tracks: List[ReleaseTrack] = []
        self.claimed: set[str] = set()

    def add_track(self, track: ReleaseTrack) -> None:
        self.tracks.append(track)

    def mark_claimed(self, recording_id: Optional[str]) -> None:
        if recording_id:
            self.claimed.add(recording_id)

    def claim(
        self, guess: PathGuess, duration: Optional[int]
    ) -> Optional[Tuple[ReleaseTrack, float]]:
        strategies: List[Tuple[str, Callable[[ReleaseTrack], bool], float]] = []
        if guess.track_number:
            strategies.append(
                (
                    "number",
                    lambda t: t.number is not None and t.number == guess.track_number,
                    0.75,
                )
            )
        if duration:
            strategies.append(
                (
                    "duration",
                    lambda t: t.duration_seconds is not None
                    and abs(t.duration_seconds - duration) <= 5,
                    0.55,
                )
            )
        if guess.title:
            title_norm = self._normalize_title(guess.title)
            strategies.append(
                (
                    "title",
                    lambda t: t.title is not None
                    and self._normalize_title(t.title or "") == title_norm,
                    0.5,
                )
            )
        for name, predicate, confidence in strategies:
            for track in self.tracks:
                if track.recording_id in self.claimed:
                    continue
                if predicate(track):
                    self.claimed.add(track.recording_id)
                    return track, confidence
        fuzzy = self._fuzzy_title_match(guess, duration)
        if fuzzy:
            track, confidence = fuzzy
            self.claimed.add(track.recording_id)
            return track, confidence
        return None

    def _fuzzy_title_match(
        self, guess: PathGuess, duration: Optional[int]
    ) -> Optional[Tuple[ReleaseTrack, float]]:
        if not guess.title:
            return None
        normalized_guess = self._normalize_title(guess.title)
        if not normalized_guess:
            return None
        best_track: Optional[ReleaseTrack] = None
        best_score = 0.0
        for track in self.tracks:
            if track.recording_id in self.claimed or not track.title:
                continue
            normalized_track = self._normalize_title(track.title)
            if not normalized_track:
                continue
            ratio = difflib.SequenceMatcher(
                None, normalized_guess, normalized_track
            ).ratio()
            if ratio <= best_score:
                continue
            if duration and track.duration_seconds:
                if abs(track.duration_seconds - duration) > max(
                    15, int(0.25 * track.duration_seconds)
                ):
                    continue
            best_track = track
            best_score = ratio
        if best_track and best_score >= 0.55:
            confidence = 0.45 + (best_score - 0.55) * 0.4
            return best_track, min(0.85, confidence)
        return None

    @staticmethod
    def _normalize_title(value: str) -> Optional[str]:
        from ..match_utils import normalize_title_for_match

        cleaned = normalize_title_for_match(value)
        if not cleaned:
            return None
        return cleaned or None


@dataclass(slots=True)
class ReleaseMatch:
    release: ReleaseData
    track: ReleaseTrack
    confidence: float


class ReleaseTracker:
    def __init__(self) -> None:
        self.dir_release: Dict[Path, tuple[str, float]] = {}
        self.releases: Dict[str, ReleaseData] = {}

    def register(
        self,
        album_dir: Path,
        release_id: Optional[str],
        fetch_release: Callable[[str], Optional[ReleaseData]],
        matched_recording_id: Optional[str] = None,
    ) -> None:
        if not release_id:
            return
        if album_dir not in self.dir_release:
            self.dir_release[album_dir] = (release_id, 0.0)
        if release_id not in self.releases:
            release = fetch_release(release_id)
            if not release:
                return
            self.releases[release_id] = release
        if matched_recording_id:
            self.releases[release_id].mark_claimed(matched_recording_id)

    def match(
        self, album_dir: Path, guess: PathGuess, duration: Optional[int]
    ) -> Optional[ReleaseMatch]:
        entry = self.dir_release.get(album_dir)
        release_id = entry[0] if entry else None
        if not release_id:
            return None
        release = self.releases.get(release_id)
        if not release:
            return None
        claimed = release.claim(guess, duration)
        if not claimed:
            return None
        track, confidence = claimed
        return ReleaseMatch(release=release, track=track, confidence=confidence)

    def context(
        self, album_dir: Path
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], float]:
        entry = self.dir_release.get(album_dir)
        if not entry:
            return None, None, None, None, 0.0
        release_id, score = entry
        release = self.releases.get(release_id)
        if release:
            return (
                release_id,
                release.album_title,
                release.album_artist,
                release.release_date,
                score,
            )
        return release_id, None, None, None, score

    def remember_release(
        self, album_dir: Path, release_id: Optional[str], score: float
    ) -> None:
        if not release_id:
            return
        current = self.dir_release.get(album_dir)
        if current and current[1] >= score:
            return
        self.dir_release[album_dir] = (release_id, score)


class MusicBrainzClient:
    def __init__(
        self, settings: ProviderSettings, cache: Optional[MetadataCache] = None
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.release_tracker = ReleaseTracker()
        self._network_disabled_until: float = 0.0
        self._last_network_warning: float = 0.0
        if musicbrainzngs is not None:
            musicbrainzngs.set_useragent(
                "audio-meta",
                "0.1",
                contact=settings.musicbrainz_useragent,
            )

    def enrich(self, meta: TrackMetadata) -> Optional[LookupResult]:
        if self._network_disabled_until and time.time() < self._network_disabled_until:
            return None
        guess = guess_metadata_from_path(meta.path)
        album_dir = meta.path.parent
        (
            dir_release_id,
            dir_release_title,
            dir_release_artist,
            dir_release_date,
            dir_release_score,
        ) = self.release_tracker.context(album_dir)
        duration, fingerprint = self._fingerprint(meta)
        if duration:
            meta.duration_seconds = duration
        else:
            meta.duration_seconds = meta.duration_seconds or self._probe_duration(
                meta.path
            )
        if fingerprint and duration:
            meta.fingerprint = fingerprint
            result = self._lookup_by_fingerprint(
                meta,
                duration,
                fingerprint,
                dir_release_id=dir_release_id,
                dir_release_title=dir_release_title,
                dir_release_artist=dir_release_artist,
                dir_release_score=dir_release_score,
            )
            if result:
                meta.match_confidence = result.score
                self._after_match(meta)
                return result

        tags = self._read_basic_tags(meta.path)
        if tags:
            result = self._lookup_by_metadata(
                meta,
                tags,
                dir_release_id=dir_release_id,
                dir_release_title=dir_release_title,
                dir_release_artist=dir_release_artist,
                dir_release_score=dir_release_score,
            )
            if result:
                meta.match_confidence = result.score
                self._after_match(meta)
                logger.debug(
                    "Metadata fallback matched %s via %s - %s",
                    meta.path,
                    tags.get("artist"),
                    tags.get("title"),
                )
                return result

        guess_result = self._lookup_by_guess(
            meta,
            guess,
            dir_release_id=dir_release_id,
            dir_release_title=dir_release_title,
            dir_release_artist=dir_release_artist,
            dir_release_score=dir_release_score,
        )
        if guess_result:
            meta.match_confidence = guess_result.score
            self._after_match(meta)
            logger.debug(
                "Guessed metadata matched %s via filename inference", meta.path
            )
            return guess_result

        release_match = self.release_tracker.match(
            meta.path.parent, guess, meta.duration_seconds
        )
        if release_match:
            lookup = self.apply_release_match(meta, release_match)
            if lookup:
                return lookup
        return None

    def _run_with_retries(self, fn, *, label: str, path: Path):
        retries = int(getattr(self.settings, "network_retries", 1) or 0)
        backoff = float(getattr(self.settings, "network_retry_backoff_seconds", 0.5) or 0.0)
        attempts = max(1, 1 + retries)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except Exception as exc:
                if not self._is_transient_network_error(exc):
                    raise
                last_exc = exc
                if attempt >= attempts:
                    break
                sleep_for = max(0.0, backoff) * (2 ** (attempt - 1))
                if sleep_for:
                    time.sleep(sleep_for)
        if last_exc:
            self._note_network_failure(label=label, path=path, exc=last_exc)
        return None

    def _note_network_failure(self, *, label: str, path: Path, exc: Exception) -> None:
        now = time.time()
        # Avoid spamming logs when DNS/network is down: warn at most once per ~10s
        # and skip further network calls for a short cooldown.
        cooldown = 30.0
        self._network_disabled_until = max(self._network_disabled_until, now + cooldown)
        if now - self._last_network_warning >= 10.0:
            self._last_network_warning = now
            logger.warning("%s failed for %s: %s", label, path, exc)

    @staticmethod
    def _is_transient_network_error(exc: Exception) -> bool:
        if isinstance(exc, (socket.gaierror, urllib.error.URLError, TimeoutError, ConnectionError)):
            return True
        if musicbrainzngs is not None:
            network_err = getattr(musicbrainzngs, "NetworkError", None)
            if network_err and isinstance(exc, network_err):
                return True
        return False

    def _lookup_by_fingerprint(
        self,
        meta: TrackMetadata,
        duration: int,
        fingerprint: str,
        dir_release_id: Optional[str] = None,
        dir_release_title: Optional[str] = None,
        dir_release_artist: Optional[str] = None,
        dir_release_score: float = 0.0,
    ) -> Optional[LookupResult]:
        if acoustid is None:
            return None
        tags = self._read_basic_tags(meta.path)
        album_hint = self._album_hint(meta, tags)
        def _lookup():
            return acoustid.lookup(
                self.settings.acoustid_api_key,
                fingerprint,
                duration,
            )

        try:
            acoustic_matches = self._run_with_retries(
                _lookup, label="AcoustID lookup", path=meta.path
            )
        except Exception as exc:
            logger.warning("AcoustID lookup failed for %s: %s", meta.path, exc)
            return None
        if not acoustic_matches:
            return None
        for score, recording_id, title, artist in self._iter_acoustid(acoustic_matches):
            before_extra = dict(meta.extra)
            recording = self._fetch_recording(recording_id, meta.path)
            if not recording:
                continue
            self._apply_recording(
                meta,
                recording,
                title,
                artist,
                preferred_release_id=dir_release_id,
                release_hint_title=dir_release_title,
                release_hint_artist=dir_release_artist,
                album_hint=album_hint or dir_release_title,
            )
            meta.extra = before_extra
            meta.match_source = "acoustid"
            meta.acoustid_id = recording_id
            logger.debug(
                "Fingerprint matched %s (recording %s score %.2f)",
                meta.path,
                recording_id,
                score,
            )
            self.release_tracker.remember_release(
                meta.path.parent, meta.musicbrainz_release_id, score
            )
            return LookupResult(meta, score=score)
        return None

    def _lookup_by_metadata(
        self,
        meta: TrackMetadata,
        tags: dict[str, Optional[str]],
        dir_release_id: Optional[str] = None,
        dir_release_title: Optional[str] = None,
        dir_release_artist: Optional[str] = None,
        dir_release_score: float = 0.0,
    ) -> Optional[LookupResult]:
        if musicbrainzngs is None:
            return None
        artist = tags.get("artist")
        title = tags.get("title")
        if not artist or not title:
            return None
        release = tags.get("album")
        def _search():
            return musicbrainzngs.search_recordings(
                artist=artist,
                recording=title,
                release=release,
                limit=1,
            )

        try:
            response = self._run_with_retries(
                _search, label="MusicBrainz recording search", path=meta.path
            )
        except Exception as exc:
            if (
                musicbrainzngs is not None
                and getattr(musicbrainzngs, "ResponseError", None)
                and isinstance(exc, musicbrainzngs.ResponseError)
            ):
                logger.warning("MusicBrainz search failed for %s: %s", meta.path, exc)
                return None
            if self._is_transient_network_error(exc):
                self._note_network_failure(
                    label="MusicBrainz recording search", path=meta.path, exc=exc
                )
                return None
            raise
        if not response:
            return None
        recordings = response.get("recording-list", [])
        if not recordings:
            return None
        best = recordings[0]
        recording = self._fetch_recording(best["id"], meta.path)
        if not recording:
            return None
        album_hint = self._album_hint(meta, tags)
        release_id, release_title, release_artist = self._extract_release(
            best, recording, album_hint=album_hint
        )
        fallback_album = tags.get("album")
        fallback_artist = tags.get("album_artist") or tags.get("artist")
        preferred_release_id = dir_release_id or release_id
        release_hint_title = dir_release_title or release_title or fallback_album
        release_hint_artist = dir_release_artist or release_artist or fallback_artist
        self._apply_recording(
            meta,
            recording,
            best.get("title"),
            self._first_artist(recording),
            preferred_release_id=preferred_release_id,
            release_hint_title=release_hint_title,
            release_hint_artist=release_hint_artist,
            album_hint=album_hint or release_hint_title,
        )
        if not meta.album and fallback_album:
            meta.album = fallback_album
        if not meta.album_artist and fallback_artist:
            meta.album_artist = fallback_artist
        score = float(best.get("ext-score", 0)) / 100.0
        meta.musicbrainz_track_id = best["id"]
        self.release_tracker.remember_release(
            meta.path.parent, meta.musicbrainz_release_id, score
        )
        return LookupResult(meta, score=score)

    def _lookup_by_guess(
        self,
        meta: TrackMetadata,
        guess: PathGuess,
        dir_release_id: Optional[str] = None,
        dir_release_title: Optional[str] = None,
        dir_release_artist: Optional[str] = None,
        dir_release_score: float = 0.0,
    ) -> Optional[LookupResult]:
        if musicbrainzngs is None:
            return None
        if guess.confidence() < 0.4 or not guess.title:
            return None
        query: Dict[str, str] = {"recording": guess.title}
        if guess.artist:
            query["artist"] = guess.artist
        if guess.album:
            query["release"] = guess.album
        def _search():
            return musicbrainzngs.search_recordings(limit=3, **query)

        try:
            response = self._run_with_retries(
                _search, label="MusicBrainz filename search", path=meta.path
            )
        except Exception as exc:
            if (
                musicbrainzngs is not None
                and getattr(musicbrainzngs, "ResponseError", None)
                and isinstance(exc, musicbrainzngs.ResponseError)
            ):
                logger.debug(
                    "Filename guess search failed for %s: %s", meta.path, exc
                )
                return None
            if self._is_transient_network_error(exc):
                self._note_network_failure(
                    label="MusicBrainz filename search", path=meta.path, exc=exc
                )
                return None
            raise
        if not response:
            return None
        candidates = response.get("recording-list", [])
        if not candidates:
            return None
        best = candidates[0]
        recording = self._fetch_recording(best["id"], meta.path)
        if not recording:
            return None
        album_hint = guess.album or self._album_hint(meta)
        self._apply_recording(
            meta,
            recording,
            best.get("title"),
            self._first_artist(recording),
            preferred_release_id=dir_release_id,
            release_hint_title=dir_release_title,
            release_hint_artist=dir_release_artist,
            album_hint=album_hint or dir_release_title,
        )
        score = float(best.get("ext-score", 0)) / 100.0 or guess.confidence()
        self.release_tracker.remember_release(
            meta.path.parent, meta.musicbrainz_release_id, score
        )
        return LookupResult(meta, score=score)

    def _album_hint(
        self, meta: TrackMetadata, tags: Optional[dict[str, Optional[str]]] = None
    ) -> Optional[str]:
        hints: List[str] = []
        if meta.album:
            hints.append(meta.album)
        if tags:
            album_tag = tags.get("album")
            if album_tag:
                hints.append(album_tag)
        guess = guess_metadata_from_path(meta.path)
        if guess.album:
            hints.append(guess.album)
        parent = meta.path.parent.name
        if parent:
            hints.append(parent)
        for value in hints:
            if value:
                cleaned = value.strip()
                if cleaned:
                    return cleaned
        return None

    def _fingerprint(self, meta: TrackMetadata) -> tuple[Optional[int], Optional[str]]:
        if acoustid is None:
            return None, None
        try:
            duration, fingerprint = acoustid.fingerprint_file(str(meta.path))
            return duration, fingerprint
        except Exception as exc:
            logger.error("Fingerprint failed for %s: %s", meta.path, exc)
            return None, None

    def _probe_duration(self, path: Path) -> Optional[int]:
        if MutagenFile is None:
            return None
        try:
            audio = MutagenFile(path)
        except Exception:
            return None
        if not audio or not getattr(audio, "info", None):
            return None
        length = getattr(audio.info, "length", None)
        return int(length) if length else None

    def _iter_acoustid(self, response):
        for match in response.get("results", []):
            score = float(match.get("score", 0))
            for recording in match.get("recordings", []):
                rec_id = recording.get("id")
                if not rec_id:
                    continue
                artists = recording.get("artists") or []
                artist_name = (
                    artists[0]["name"]
                    if artists and isinstance(artists[0], dict) and "name" in artists[0]
                    else None
                )
                yield score, rec_id, recording.get("title"), artist_name

    def _apply_recording(
        self,
        meta: TrackMetadata,
        recording: dict,
        title: Optional[str],
        artist: Optional[str],
        preferred_release_id: Optional[str] = None,
        release_hint_title: Optional[str] = None,
        release_hint_artist: Optional[str] = None,
        album_hint: Optional[str] = None,
    ) -> None:
        release = self._select_release(recording, preferred_release_id, album_hint)
        if not release and (preferred_release_id or release_hint_title):
            release = {
                "id": preferred_release_id,
                "title": release_hint_title or album_hint,
                "artist-credit": [{"name": release_hint_artist}]
                if release_hint_artist
                else [],
            }
        meta.title = title or recording.get("title")
        meta.artist = self._normalize_artists(artist or self._first_artist(recording))
        meta.album = release.get("title") if isinstance(release, dict) else meta.album
        release_artist = self._normalize_artists(self._first_artist(release))
        meta.album_artist = release_artist or meta.artist
        meta.musicbrainz_track_id = recording.get("id")
        meta.musicbrainz_release_id = (
            release.get("id")
            if isinstance(release, dict)
            else meta.musicbrainz_release_id
        )
        work_rels = recording.get("work-relation-list", [])
        if work_rels:
            work = work_rels[0].get("work", {})
            meta.work = work.get("title")
            meta.composer = self._first_artist(work)
        self._capture_performers(recording, meta)

    def _first_artist(self, entity: dict) -> Optional[str]:
        credits = entity.get("artist-credit", [])
        if credits:
            names: List[str] = []
            for credit in credits:
                if isinstance(credit, str):
                    if credit.strip():
                        names.append(credit.strip())
                elif isinstance(credit, dict):
                    if "name" in credit:
                        names.append(credit["name"])
                    elif isinstance(credit.get("artist"), dict) and credit[
                        "artist"
                    ].get("name"):
                        names.append(credit["artist"]["name"])
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

    def _select_release(
        self,
        recording: dict,
        preferred_release_id: Optional[str],
        album_hint: Optional[str],
    ) -> dict:
        release_list = recording.get("release-list") or recording.get("releases") or []
        if preferred_release_id:
            for release in release_list:
                if release.get("id") == preferred_release_id:
                    return release
        candidate = self._choose_release_candidate(release_list, album_hint)
        return candidate or {}

    def _choose_release_candidate(
        self,
        releases: List[dict],
        album_hint: Optional[str],
    ) -> Optional[dict]:
        if not releases:
            return None
        if album_hint:
            best_release = None
            best_score = 0.0
            for release in releases:
                score = self._title_similarity(album_hint, release.get("title"))
                if score > best_score:
                    best_release = release
                    best_score = score
            if best_release and best_score >= 0.45:
                return best_release
        return releases[0]

    def _normalize_artists(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        tokens = [chunk.strip() for chunk in re.split(r"[;,]+", value) if chunk.strip()]
        connectors = {"&", "and", "with", "feat", "featuring", "+"}
        unique: List[str] = []
        for token in tokens:
            base = token.split(" (", 1)[0].strip()
            if not base:
                continue
            if base.lower() in connectors:
                continue
            if base not in unique:
                unique.append(base)
        return ", ".join(unique) if unique else None

    def _title_similarity(self, first: Optional[str], second: Optional[str]) -> float:
        if not first or not second:
            return 0.0
        return difflib.SequenceMatcher(None, first.lower(), second.lower()).ratio()

    def _fetch_recording(self, recording_id: str, path) -> Optional[dict]:
        try:
            if musicbrainzngs is None:
                return None
            if self.cache:
                cached = self.cache.get_recording(recording_id)
                if cached:
                    logger.debug("MusicBrainz cache hit for recording %s", recording_id)
                    return cached
            def _fetch():
                return musicbrainzngs.get_recording_by_id(
                    recording_id,
                    includes=["artists", "releases", "work-rels", "artist-credits"],
                )["recording"]

            recording = self._run_with_retries(
                _fetch, label="MusicBrainz recording fetch", path=Path(path)
            )
            if not recording:
                return None
            if self.cache:
                self.cache.set_recording(recording_id, recording)
            return recording
        except musicbrainzngs.ResponseError as exc:
            logger.warning("MusicBrainz error for %s (%s): %s", path, recording_id, exc)
            return None

    def _fetch_release_tracks(self, release_id: str) -> Optional[ReleaseData]:
        if self.cache:
            cached = self.cache.get_release(release_id)
            if cached:
                logger.debug("MusicBrainz cache hit for release %s", release_id)
                return self._build_release_data(cached)
        try:
            if musicbrainzngs is None:
                return None
            def _fetch():
                return musicbrainzngs.get_release_by_id(
                    release_id,
                    includes=["recordings", "artist-credits", "media"],
                )["release"]

            release = self._run_with_retries(
                _fetch, label="MusicBrainz release fetch", path=Path(release_id)
            )
            if not release:
                return None
        except musicbrainzngs.ResponseError as exc:
            logger.debug("Failed to load release %s: %s", release_id, exc)
            return None
        if self.cache:
            self.cache.set_release(release_id, release)
        return self._build_release_data(release)

    def _build_release_data(self, release: dict) -> ReleaseData:
        release_id = release.get("id")
        if not release_id:
            raise ValueError("release payload missing id")
        data = ReleaseData(
            release_id,
            release.get("title"),
            self._first_artist(release),
            release.get("date"),
        )
        media = release.get("medium-list", [])
        data.disc_count = len(media)
        for medium_index, medium in enumerate(media, start=1):
            formats = medium.get("format-list") or (
                [medium.get("format")] if medium.get("format") else []
            )
            for fmt in formats:
                if fmt and fmt not in data.formats:
                    data.formats.append(fmt)
            track_list = medium.get("track-list", []) or []
            raw_numbers = [t.get("number") for t in track_list]
            parsed_numbers = [self._parse_track_number(v) for v in raw_numbers]
            has_letters = any(
                isinstance(v, str) and re.search(r"[A-Za-z]", v) for v in raw_numbers
            )
            resolved_numbers: list[Optional[int]] = []
            if track_list:
                if all(n is None for n in parsed_numbers):
                    resolved_numbers = list(range(1, len(track_list) + 1))
                elif any(n is None for n in parsed_numbers):
                    used = {n for n in parsed_numbers if isinstance(n, int)}
                    next_candidate = 1
                    for n in parsed_numbers:
                        if isinstance(n, int):
                            resolved_numbers.append(n)
                            continue
                        while next_candidate in used:
                            next_candidate += 1
                        resolved_numbers.append(next_candidate)
                        used.add(next_candidate)
                        next_candidate += 1
                elif has_letters and len(set(parsed_numbers)) != len(parsed_numbers):
                    resolved_numbers = list(range(1, len(track_list) + 1))
                else:
                    resolved_numbers = parsed_numbers

            for index, track in enumerate(track_list):
                recording = track.get("recording", {})
                number = (
                    resolved_numbers[index]
                    if index < len(resolved_numbers)
                    else self._parse_track_number(track.get("number"))
                )
                length = track.get("length")
                duration = int(length) // 1000 if length else None
                data.add_track(
                    ReleaseTrack(
                        recording_id=recording.get("id"),
                        disc_number=medium_index,
                        number=number,
                        title=recording.get("title"),
                        duration_seconds=duration,
                    )
                )
        return data

    def apply_release_match(
        self, meta: TrackMetadata, release_match: ReleaseMatch
    ) -> Optional[LookupResult]:
        recording = self._fetch_recording(release_match.track.recording_id, meta.path)
        if not recording:
            return None
        self._apply_recording(
            meta,
            recording,
            release_match.track.title or recording.get("title"),
            self._first_artist(recording),
            preferred_release_id=release_match.release.release_id,
            release_hint_title=release_match.release.album_title,
            release_hint_artist=release_match.release.album_artist,
            album_hint=release_match.release.album_title,
        )
        score = release_match.confidence
        meta.match_confidence = max(meta.match_confidence or 0.0, score)
        self._after_match(meta)
        logger.debug(
            "Release memory matched %s as track %s of release %s",
            meta.path,
            release_match.track.title,
            release_match.release.release_id,
        )
        self.release_tracker.remember_release(
            meta.path.parent, meta.musicbrainz_release_id, score
        )
        return LookupResult(meta, score=score)

    def _after_match(self, meta: TrackMetadata) -> None:
        release_id = meta.musicbrainz_release_id
        if not release_id:
            return
        self.release_tracker.register(
            meta.path.parent,
            release_id,
            self._fetch_release_tracks,
            matched_recording_id=meta.musicbrainz_track_id,
        )
        release = self.release_tracker.releases.get(release_id)
        if release:
            if not meta.album:
                meta.album = release.album_title
            if not meta.album_artist:
                meta.album_artist = release.album_artist

    def _read_basic_tags(self, path) -> dict[str, Optional[str]]:
        if MutagenFile is None:
            return {}
        try:
            audio = MutagenFile(path, easy=True)
        except Exception as exc:  # pragma: no cover - tag parsing failures
            logger.debug("Failed to read tags from %s: %s", path, exc)
            return {}
        if not audio or not audio.tags:
            return {}
        return {
            "artist": self._first_tag(audio, ["artist", "albumartist"]),
            "title": self._first_tag(audio, ["title"]),
            "album": self._first_tag(audio, ["album"]),
        }

    @staticmethod
    def _first_tag(audio, keys) -> Optional[str]:
        for key in keys:
            values = audio.tags.get(key)
            if values:
                if isinstance(values, list):
                    return values[0]
                return values
        return None

    @staticmethod
    def _parse_track_number(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.isdigit():
            return int(cleaned)
        match = re.match(r"^\s*\d+\s*[-./]\s*(\d+)\s*$", cleaned)
        if match:
            return int(match.group(1))
        match = re.match(r"^\s*([A-Za-z])\s*$", cleaned)
        if match:
            return ord(match.group(1).upper()) - ord("A") + 1
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        return int(digits) if digits else None

    def _extract_release(
        self,
        search_recording: dict,
        recording: dict,
        album_hint: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        release_list = recording.get("release-list") or recording.get("releases") or []
        candidate = self._choose_release_candidate(release_list, album_hint)
        if candidate and candidate.get("id"):
            return (
                candidate.get("id"),
                candidate.get("title"),
                self._first_artist(candidate),
            )
        candidate = self._choose_release_candidate(
            search_recording.get("release-list", []), album_hint
        )
        if candidate and candidate.get("id"):
            return (
                candidate.get("id"),
                candidate.get("title"),
                self._first_artist(candidate),
            )
        return None, None, None

    def search_release_candidates(
        self,
        artist_hint: Optional[str],
        album_hint: Optional[str],
        limit: int = 5,
    ) -> List[dict]:
        if musicbrainzngs is None:
            return []
        query: Dict[str, str] = {}
        if artist_hint:
            query["artist"] = artist_hint
        if album_hint:
            query["release"] = album_hint
        if not query:
            return []
        def _search():
            return musicbrainzngs.search_releases(limit=limit, **query)

        try:
            response = self._run_with_retries(
                _search,
                label="MusicBrainz release search",
                path=Path(f"{artist_hint or ''}/{album_hint or ''}"),
            )
        except Exception as exc:
            if (
                musicbrainzngs is not None
                and getattr(musicbrainzngs, "ResponseError", None)
                and isinstance(exc, musicbrainzngs.ResponseError)
            ):
                logger.debug(
                    "Release search failed for %s/%s: %s",
                    artist_hint,
                    album_hint,
                    exc,
                )
                return []
            if self._is_transient_network_error(exc):
                self._note_network_failure(
                    label="MusicBrainz release search", path=Path("."), exc=exc
                )
                return []
            raise
        if not response:
            return []
        releases = response.get("release-list", [])
        candidates: List[dict] = []
        for entry in releases:
            release_id = entry.get("id")
            if not release_id:
                continue
            data = self._fetch_release_tracks(release_id)
            if data and release_id not in self.release_tracker.releases:
                self.release_tracker.releases[release_id] = data
            title = (
                data.album_title if data and data.album_title else entry.get("title")
            )
            artist = (
                data.album_artist
                if data and data.album_artist
                else self._first_artist(entry)
            )
            date = (
                data.release_date if data and data.release_date else entry.get("date")
            )
            track_total = len(data.tracks) if data and data.tracks else None
            disc_count = data.disc_count if data else None
            formats = list(data.formats) if data else []
            score = (
                float(entry.get("ext-score", 0)) / 100.0
                if entry.get("ext-score")
                else 0.0
            )
            candidates.append(
                {
                    "id": release_id,
                    "title": title,
                    "artist": artist,
                    "date": date,
                    "score": score,
                    "track_total": track_total,
                    "disc_count": disc_count,
                    "formats": formats,
                }
            )
        return candidates
