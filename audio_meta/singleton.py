"""
Unified singleton-resolution workflow.

Determines whether a singleton track is a legitimate single release or
a misplaced album track, and if misplaced, identifies where it belongs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Any

from .cache import MetadataCache
from .heuristics import guess_metadata_from_path
from .match_utils import normalize_match_text, title_similarity, duration_similarity
from .models import TrackMetadata

logger = logging.getLogger(__name__)


class SingletonType(Enum):
    """Classification of singleton track type."""
    LEGITIMATE_SINGLE = "legitimate_single"
    MISPLACED_ALBUM_TRACK = "misplaced_album_track"
    ORPHANED_TRACK = "orphaned_track"
    UNKNOWN = "unknown"


class SingletonCause(Enum):
    """Root cause diagnosis for why a track is a singleton."""
    ALBUM_MOVED_WITHOUT_TRACK = "album_moved_without_track"
    INCORRECT_TAGGING = "incorrect_tagging"
    LEGITIMATE_SINGLE_RELEASE = "legitimate_single_release"
    PARTIAL_DOWNLOAD = "partial_download"
    COMPILATION_TRACK = "compilation_track"
    UNKNOWN = "unknown"


@dataclass
class SingletonCandidate:
    """A potential destination for a singleton track."""
    directory: Path
    release_id: Optional[str] = None
    provider: str = ""
    track_count: int = 0
    confidence: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    
    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.85


@dataclass
class SingletonResolution:
    """Result of singleton resolution workflow."""
    singleton_type: SingletonType
    cause: SingletonCause
    candidates: list[SingletonCandidate] = field(default_factory=list)
    best_candidate: Optional[SingletonCandidate] = None
    explanation: str = ""
    auto_resolvable: bool = False
    
    @property
    def should_prompt(self) -> bool:
        """Returns True if user input is needed."""
        return not self.auto_resolvable and self.singleton_type != SingletonType.LEGITIMATE_SINGLE


@dataclass
class TrackSignals:
    """Signals extracted from a singleton track for matching."""
    # From metadata
    artist: Optional[str] = None
    album_artist: Optional[str] = None
    album: Optional[str] = None
    title: Optional[str] = None
    composer: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration_seconds: Optional[int] = None
    
    # From path/filename
    path_artist: Optional[str] = None
    path_album: Optional[str] = None
    path_track_number: Optional[int] = None
    path_title: Optional[str] = None
    
    # From fingerprint
    fingerprint: Optional[str] = None
    acoustid_id: Optional[str] = None
    musicbrainz_recording_id: Optional[str] = None
    musicbrainz_release_id: Optional[str] = None
    
    # Computed
    has_track_number: bool = False
    suggests_album_track: bool = False
    suggests_single: bool = False


class SingletonResolver:
    """
    Unified singleton-resolution workflow that determines the appropriate
    action for singleton tracks.
    """
    
    # Minimum confidence for auto-resolution
    AUTO_RESOLVE_THRESHOLD = 0.90
    
    # Minimum confidence to consider a match valid
    MATCH_THRESHOLD = 0.65
    
    def __init__(
        self,
        cache: MetadataCache,
        musicbrainz: Optional[Any] = None,
        discogs: Optional[Any] = None,
        extensions: Optional[set[str]] = None,
        library_roots: Optional[list[Path]] = None,
    ) -> None:
        self.cache = cache
        self.musicbrainz = musicbrainz
        self.discogs = discogs
        self.extensions = extensions or {".mp3", ".flac", ".m4a"}
        self.library_roots = library_roots or []
    
    def resolve(
        self,
        directory: Path,
        meta: TrackMetadata,
        *,
        existing_tags: Optional[dict[str, Optional[str]]] = None,
    ) -> SingletonResolution:
        """
        Run the complete singleton resolution workflow.
        
        Args:
            directory: The singleton directory
            meta: Track metadata
            existing_tags: Pre-read file tags
            
        Returns:
            SingletonResolution with classification and candidates
        """
        # Extract all available signals
        signals = self._extract_signals(meta, existing_tags)
        
        # Classify the singleton
        singleton_type, cause = self._classify(signals, meta)
        
        # If it's a legitimate single, we're done
        if singleton_type == SingletonType.LEGITIMATE_SINGLE:
            return SingletonResolution(
                singleton_type=singleton_type,
                cause=cause,
                explanation="Track identified as a legitimate single release",
                auto_resolvable=True,
            )
        
        # Find candidate destinations
        candidates = self._find_candidates(directory, meta, signals)
        
        # Select best candidate
        best = self._select_best_candidate(candidates, signals)
        
        # Determine if auto-resolvable
        auto_resolvable = (
            best is not None 
            and best.confidence >= self.AUTO_RESOLVE_THRESHOLD
            and len(candidates) == 1
        )
        
        # Build explanation
        explanation = self._build_explanation(singleton_type, cause, best, candidates)
        
        return SingletonResolution(
            singleton_type=singleton_type,
            cause=cause,
            candidates=candidates,
            best_candidate=best,
            explanation=explanation,
            auto_resolvable=auto_resolvable,
        )
    
    def _extract_signals(
        self,
        meta: TrackMetadata,
        existing_tags: Optional[dict[str, Optional[str]]],
    ) -> TrackSignals:
        """Extract all available signals from a track."""
        signals = TrackSignals()
        
        # From metadata object
        signals.artist = meta.artist
        signals.album_artist = meta.album_artist
        signals.album = meta.album
        signals.title = meta.title
        signals.composer = meta.composer
        signals.track_number = meta.track_number
        signals.disc_number = meta.disc_number
        signals.duration_seconds = meta.duration_seconds
        signals.fingerprint = meta.fingerprint
        signals.acoustid_id = meta.acoustid_id
        signals.musicbrainz_release_id = meta.musicbrainz_release_id
        signals.musicbrainz_recording_id = meta.musicbrainz_track_id
        
        # From existing tags
        if existing_tags:
            if not signals.track_number:
                raw = existing_tags.get("tracknumber") or existing_tags.get("track_number")
                if raw:
                    signals.track_number = self._parse_track_number(raw)
        
        # From path heuristics
        guess = guess_metadata_from_path(meta.path)
        signals.path_artist = guess.artist
        signals.path_album = guess.album
        signals.path_track_number = guess.track_number
        signals.path_title = guess.title
        
        # Computed signals
        signals.has_track_number = (
            signals.track_number is not None 
            or signals.path_track_number is not None
        )
        
        # Check if filename suggests album track (e.g., "01 - Title.mp3")
        filename = meta.path.stem
        signals.suggests_album_track = bool(
            signals.has_track_number
            or re.match(r"^\d{1,2}\s*[-_.]\s*", filename)
            or re.match(r"^\d{1,2}\s+", filename)
        )
        
        # Check if metadata suggests single release
        album_lower = (signals.album or "").lower()
        signals.suggests_single = bool(
            "single" in album_lower
            or "ep" in album_lower.split()
            or "(single)" in album_lower
        )
        
        return signals
    
    def _classify(
        self,
        signals: TrackSignals,
        meta: TrackMetadata,
    ) -> tuple[SingletonType, SingletonCause]:
        """Classify the singleton type and diagnose the cause."""
        
        # Check for legitimate single indicators
        if signals.suggests_single:
            return SingletonType.LEGITIMATE_SINGLE, SingletonCause.LEGITIMATE_SINGLE_RELEASE
        
        # If we have a track number, it's likely a misplaced album track
        if signals.has_track_number:
            effective_track = signals.track_number or signals.path_track_number
            if effective_track and effective_track > 1:
                # Track number > 1 strongly suggests album track
                return SingletonType.MISPLACED_ALBUM_TRACK, SingletonCause.ALBUM_MOVED_WITHOUT_TRACK
            elif effective_track == 1:
                # Track 1 could be either - need more signals
                pass
        
        # Check MusicBrainz release type
        if signals.musicbrainz_release_id and self.musicbrainz:
            release_data = self._get_mb_release_info(signals.musicbrainz_release_id)
            if release_data:
                release_type = release_data.get("release-group", {}).get("primary-type", "").lower()
                if release_type == "single":
                    return SingletonType.LEGITIMATE_SINGLE, SingletonCause.LEGITIMATE_SINGLE_RELEASE
                elif release_type in ("album", "ep"):
                    track_count = self._count_mb_release_tracks(release_data)
                    if track_count and track_count > 3:
                        return SingletonType.MISPLACED_ALBUM_TRACK, SingletonCause.ALBUM_MOVED_WITHOUT_TRACK
        
        # Check Discogs for single releases
        if self.discogs and signals.title:
            is_single = self._check_discogs_single(signals)
            if is_single:
                return SingletonType.LEGITIMATE_SINGLE, SingletonCause.LEGITIMATE_SINGLE_RELEASE
        
        # Check if album exists on disk with missing track
        if signals.album and self._album_exists_missing_track(signals):
            return SingletonType.MISPLACED_ALBUM_TRACK, SingletonCause.ALBUM_MOVED_WITHOUT_TRACK
        
        # If tags are inconsistent with path, likely tagging issue
        if self._tags_inconsistent_with_path(signals):
            return SingletonType.MISPLACED_ALBUM_TRACK, SingletonCause.INCORRECT_TAGGING
        
        # Default to unknown
        return SingletonType.UNKNOWN, SingletonCause.UNKNOWN
    
    def _find_candidates(
        self,
        directory: Path,
        meta: TrackMetadata,
        signals: TrackSignals,
    ) -> list[SingletonCandidate]:
        """Find potential destination directories for the singleton."""
        candidates: list[SingletonCandidate] = []
        
        # 1. Check release home from cache
        if signals.musicbrainz_release_id:
            release_key = f"musicbrainz:{signals.musicbrainz_release_id}"
            home = self.cache.get_release_home(release_key)
            if home:
                home_dir_str, track_count, _ = home
                home_dir = Path(home_dir_str)
                if home_dir.exists() and home_dir != directory:
                    candidate = SingletonCandidate(
                        directory=home_dir,
                        release_id=signals.musicbrainz_release_id,
                        provider="musicbrainz",
                        track_count=track_count or self._count_audio_files(home_dir),
                        confidence=0.85,
                        match_reasons=["Release home in cache"],
                    )
                    candidates.append(candidate)
        
        # 2. Check directories for the same release ID
        if signals.musicbrainz_release_id:
            dirs = self.cache.find_directories_for_release(signals.musicbrainz_release_id)
            for dir_str in dirs:
                dir_path = Path(dir_str)
                if dir_path == directory or not dir_path.exists():
                    continue
                # Avoid duplicates
                if any(c.directory == dir_path for c in candidates):
                    continue
                count = self._count_audio_files(dir_path)
                if count >= 3:  # Likely an album directory
                    candidate = SingletonCandidate(
                        directory=dir_path,
                        release_id=signals.musicbrainz_release_id,
                        provider="musicbrainz",
                        track_count=count,
                        confidence=0.80,
                        match_reasons=["Same release ID"],
                    )
                    candidates.append(candidate)
        
        # 3. Search by artist/album consistency
        if signals.album and (signals.artist or signals.album_artist):
            artist_key = self._normalize_token(signals.album_artist or signals.artist)
            album_key = self._normalize_token(signals.album)
            matching = self._find_matching_album_directories(artist_key, album_key, directory)
            for match_dir, score, reasons in matching:
                if any(c.directory == match_dir for c in candidates):
                    # Update confidence if better
                    for c in candidates:
                        if c.directory == match_dir:
                            c.confidence = max(c.confidence, score)
                            c.match_reasons.extend(reasons)
                    continue
                candidate = SingletonCandidate(
                    directory=match_dir,
                    track_count=self._count_audio_files(match_dir),
                    confidence=score,
                    match_reasons=reasons,
                )
                candidates.append(candidate)
        
        # 4. Check for track number gaps
        if signals.has_track_number and candidates:
            track_num = signals.track_number or signals.path_track_number
            for candidate in candidates:
                if self._directory_missing_track(candidate.directory, track_num):
                    candidate.confidence += 0.15
                    candidate.match_reasons.append(f"Missing track {track_num}")
        
        # Sort by confidence
        candidates.sort(key=lambda c: -c.confidence)
        
        return candidates
    
    def _select_best_candidate(
        self,
        candidates: list[SingletonCandidate],
        signals: TrackSignals,
    ) -> Optional[SingletonCandidate]:
        """Select the best candidate from the list."""
        if not candidates:
            return None
        
        # Filter by minimum threshold
        valid = [c for c in candidates if c.confidence >= self.MATCH_THRESHOLD]
        if not valid:
            return None
        
        # If only one valid candidate, return it
        if len(valid) == 1:
            return valid[0]
        
        # Apply secondary validation
        best = valid[0]
        
        # Prefer candidates with matching track numbers
        if signals.has_track_number:
            track_num = signals.track_number or signals.path_track_number
            for candidate in valid:
                if self._directory_missing_track(candidate.directory, track_num):
                    if candidate.confidence >= best.confidence * 0.95:
                        best = candidate
                        break
        
        return best
    
    def _build_explanation(
        self,
        singleton_type: SingletonType,
        cause: SingletonCause,
        best: Optional[SingletonCandidate],
        candidates: list[SingletonCandidate],
    ) -> str:
        """Build a human-readable explanation of the resolution."""
        parts = []
        
        # Type explanation
        if singleton_type == SingletonType.LEGITIMATE_SINGLE:
            parts.append("This track is a legitimate single release.")
        elif singleton_type == SingletonType.MISPLACED_ALBUM_TRACK:
            parts.append("This track appears to be a misplaced album track.")
        elif singleton_type == SingletonType.ORPHANED_TRACK:
            parts.append("This track is orphaned with no clear destination.")
        else:
            parts.append("Unable to determine the track's origin.")
        
        # Cause explanation
        cause_map = {
            SingletonCause.ALBUM_MOVED_WITHOUT_TRACK: "The album was likely moved without this track.",
            SingletonCause.INCORRECT_TAGGING: "The track's tags are inconsistent with its location.",
            SingletonCause.LEGITIMATE_SINGLE_RELEASE: "Released as a single or standalone track.",
            SingletonCause.PARTIAL_DOWNLOAD: "May be from an incomplete album download.",
            SingletonCause.COMPILATION_TRACK: "Possibly from a compilation album.",
        }
        if cause in cause_map:
            parts.append(cause_map[cause])
        
        # Candidate info
        if best:
            parts.append(f"\nSuggested destination: {best.directory}")
            parts.append(f"Confidence: {best.confidence:.0%}")
            if best.match_reasons:
                parts.append(f"Reasons: {', '.join(best.match_reasons)}")
        elif candidates:
            parts.append(f"\nFound {len(candidates)} possible destination(s), but none with high confidence.")
        
        return "\n".join(parts)
    
    # Helper methods
    
    def _get_mb_release_info(self, release_id: str) -> Optional[dict]:
        """Get MusicBrainz release info from cache or API."""
        if self.cache:
            cached = self.cache.get_release(release_id)
            if cached:
                return cached
        if self.musicbrainz and hasattr(self.musicbrainz, "_fetch_release_tracks"):
            try:
                return self.musicbrainz._fetch_release_tracks(release_id)
            except Exception:
                pass
        return None
    
    def _count_mb_release_tracks(self, release_data: dict) -> Optional[int]:
        """Count tracks in a MusicBrainz release."""
        media = release_data.get("medium-list") or release_data.get("media", [])
        total = 0
        for medium in media:
            tracks = medium.get("track-list") or medium.get("tracks", [])
            total += len(tracks)
        return total if total > 0 else None
    
    def _check_discogs_single(self, signals: TrackSignals) -> bool:
        """Check if Discogs has this as a single release."""
        if not self.discogs:
            return False
        try:
            artist = signals.artist or signals.album_artist
            candidates = self.discogs.search_candidates(
                artist=artist,
                title=signals.title,
                limit=5,
            )
            for cand in candidates:
                formats = cand.get("format") or cand.get("formats") or []
                if isinstance(formats, str):
                    formats = [formats]
                for fmt in formats:
                    if isinstance(fmt, str) and "single" in fmt.lower():
                        return True
        except Exception:
            pass
        return False
    
    def _album_exists_missing_track(self, signals: TrackSignals) -> bool:
        """Check if an album exists on disk that's missing this track."""
        # This would require scanning directories - simplified version
        return False  # TODO: Implement full disk scan
    
    def _tags_inconsistent_with_path(self, signals: TrackSignals) -> bool:
        """Check if tags are inconsistent with the file's location."""
        if not signals.path_artist or not signals.artist:
            return False
        path_artist_norm = self._normalize_token(signals.path_artist)
        tag_artist_norm = self._normalize_token(signals.artist)
        if path_artist_norm and tag_artist_norm:
            if path_artist_norm != tag_artist_norm:
                # Check if they're at least similar
                sim = title_similarity(signals.path_artist, signals.artist)
                if sim and sim < 0.5:
                    return True
        return False
    
    def _find_matching_album_directories(
        self,
        artist_key: str,
        album_key: str,
        exclude: Path,
    ) -> list[tuple[Path, float, list[str]]]:
        """Find directories that might match the given artist/album."""
        matches: list[tuple[Path, float, list[str]]] = []
        
        # Search through library roots
        for root in self.library_roots:
            if not root.exists():
                continue
            for artist_dir in root.iterdir():
                if not artist_dir.is_dir():
                    continue
                artist_dir_key = self._normalize_token(artist_dir.name)
                if artist_key and artist_dir_key != artist_key:
                    # Allow partial match
                    if artist_key not in artist_dir_key and artist_dir_key not in artist_key:
                        continue
                
                for album_dir in artist_dir.iterdir():
                    if not album_dir.is_dir():
                        continue
                    if album_dir == exclude:
                        continue
                    album_dir_key = self._normalize_token(album_dir.name)
                    if album_key and album_dir_key != album_key:
                        if album_key not in album_dir_key and album_dir_key not in album_key:
                            continue
                    
                    # Count audio files
                    count = self._count_audio_files(album_dir)
                    if count < 2:
                        continue
                    
                    # Calculate score
                    score = 0.5
                    reasons = []
                    
                    if artist_dir_key == artist_key:
                        score += 0.2
                        reasons.append("Artist match")
                    elif artist_key in artist_dir_key or artist_dir_key in artist_key:
                        score += 0.1
                        reasons.append("Artist partial match")
                    
                    if album_dir_key == album_key:
                        score += 0.25
                        reasons.append("Album match")
                    elif album_key in album_dir_key or album_dir_key in album_key:
                        score += 0.1
                        reasons.append("Album partial match")
                    
                    if count >= 5:
                        score += 0.05
                        reasons.append(f"{count} tracks")
                    
                    matches.append((album_dir, min(score, 1.0), reasons))
        
        matches.sort(key=lambda x: -x[1])
        return matches[:10]  # Limit results
    
    def _directory_missing_track(self, directory: Path, track_number: Optional[int]) -> bool:
        """Check if a directory is missing the specified track number."""
        if not track_number:
            return False
        
        existing_tracks: set[int] = set()
        for file_path in directory.iterdir():
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in self.extensions:
                continue
            guess = guess_metadata_from_path(file_path)
            if guess.track_number:
                existing_tracks.add(guess.track_number)
        
        return track_number not in existing_tracks
    
    def _count_audio_files(self, directory: Path) -> int:
        """Count audio files in a directory (recursively)."""
        count = 0
        try:
            for item in directory.rglob("*"):
                if item.is_file() and item.suffix.lower() in self.extensions:
                    count += 1
        except (PermissionError, OSError):
            pass
        return count
    
    @staticmethod
    def _normalize_token(value: Optional[str]) -> str:
        """Normalize a string for comparison."""
        if not value:
            return ""
        import unicodedata
        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_only = ascii_only.lower()
        ascii_only = re.sub(r"[^a-z0-9]+", " ", ascii_only)
        ascii_only = re.sub(r"\s+", "", ascii_only)
        return ascii_only
    
    @staticmethod
    def _parse_track_number(value: str) -> Optional[int]:
        """Parse a track number from a string like '3' or '3/12'."""
        if not value:
            return None
        cleaned = value.strip()
        if "/" in cleaned:
            cleaned = cleaned.split("/", 1)[0].strip()
        if cleaned.isdigit():
            return int(cleaned)
        return None
