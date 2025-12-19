from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..daemon_types import PendingResult, ReleaseExample
from ..match_utils import normalize_match_text, parse_discogs_duration
from ..models import TrackMetadata

if TYPE_CHECKING:  # pragma: no cover
    from ..cache import MetadataCache
    from ..providers.discogs import DiscogsClient
    from ..providers.musicbrainz import MusicBrainzClient

logger = logging.getLogger(__name__)


class ReleaseMatchingService:
    """Service for matching releases, scoring candidates, and managing release homes."""

    def __init__(
        self,
        cache: MetadataCache,
        musicbrainz: MusicBrainzClient,
        discogs: Optional[DiscogsClient],
        count_audio_files_fn=None,
    ) -> None:
        self.cache = cache
        self.musicbrainz = musicbrainz
        self.discogs = discogs
        self._count_audio_files_fn = count_audio_files_fn
        self._release_sep = ":"

    def release_key(self, provider: str, release_id: str) -> str:
        """Create a composite release key from provider and ID."""
        return f"{provider}{self._release_sep}{release_id}"

    def split_release_key(self, key: str) -> tuple[str, str]:
        """Split a composite release key into provider and ID."""
        if self._release_sep in key:
            provider, release_id = key.split(self._release_sep, 1)
            return provider, release_id
        return "musicbrainz", key

    def auto_pick_equivalent_release(
        self,
        candidates: list[tuple[str, float]],
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[str]:
        """
        Auto-select a release if all candidates are equivalent.

        Candidates are considered equivalent if they have identical
        canonical signatures (track count and normalized track list).
        """
        signatures: dict[str, tuple[int, tuple[tuple[str, Optional[int]], ...]]] = {}
        for key, _ in candidates:
            signature = self._canonical_release_signature(
                key, release_examples, discogs_details
            )
            if signature is None:
                return None
            signatures[key] = signature

        iterator = iter(signatures.values())
        try:
            first_signature = next(iterator)
        except StopIteration:
            return None

        if not all(sig == first_signature for sig in iterator):
            return None

        # Prefer MusicBrainz over Discogs
        priority = {"musicbrainz": 0, "discogs": 1}
        best_key = min(
            signatures.keys(),
            key=lambda key: (priority.get(self.split_release_key(key)[0], 99), key),
        )
        return best_key

    def auto_pick_existing_release_home(
        self,
        candidates: list[tuple[str, float]],
        directory: Path,
        current_count: int,
        release_examples: dict[str, ReleaseExample],
    ) -> Optional[str]:
        """
        Auto-select a release that already has a home directory.

        Prefers releases where:
        1. A release home exists with more tracks than current directory
        2. The track count matches the release's total tracks
        3. Higher score
        4. MusicBrainz over Discogs
        """
        best_key: Optional[str] = None
        best_rank: Optional[tuple[int, int, float, int, str]] = None

        for key, score in candidates:
            provider, release_id = self.split_release_key(key)
            if provider != "musicbrainz" or not release_id:
                continue

            release_key = self.release_key(provider, release_id)
            release_home, home_count = self._release_home_for_key(
                release_key, directory, current_count
            )
            if not release_home or home_count <= 0:
                continue

            example = release_examples.get(key)
            track_total = example.track_total if example else None
            fit = abs(track_total - home_count) if track_total else 10_000

            provider_priority = 0 if provider == "musicbrainz" else 1
            rank = (home_count, -fit, float(score), -provider_priority, key)

            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_key = key

        return best_key

    def find_release_home(
        self,
        release_id: Optional[str],
        current_dir: Path,
        current_count: int,
    ) -> Optional[Path]:
        """
        Find the primary directory containing the most tracks for a release.

        Checks cache first, then searches for other directories with this release.
        """
        if not release_id:
            return None

        cached_key = self.release_key("musicbrainz", release_id)
        cached_home = self.cache.get_release_home(cached_key)
        if cached_home:
            raw_path, _, cached_hash = cached_home
            candidate = Path(raw_path)
            if candidate.exists() and candidate != current_dir:
                current_hash = self.cache.get_directory_hash(candidate)
                if cached_hash and current_hash and cached_hash != current_hash:
                    self.cache.delete_release_home(cached_key)
                else:
                    return candidate
            elif not candidate.exists():
                self.cache.delete_release_home(cached_key)

        candidates = self.cache.find_directories_for_release(release_id)
        best_dir: Optional[Path] = None
        best_count = current_count

        for raw in candidates:
            candidate = Path(raw)
            if candidate == current_dir or not candidate.exists():
                continue
            count = self._count_audio_files(candidate)
            if count > best_count:
                best_dir = candidate
                best_count = count

        return best_dir

    def canonical_release_signature(
        self,
        key: str,
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[tuple[int, tuple[tuple[str, Optional[int]], ...]]]:
        """
        Generate a canonical signature for a release.

        Signature includes track count and normalized track list with durations.
        Used for detecting equivalent releases.
        """
        return self._canonical_release_signature(key, release_examples, discogs_details)

    def _canonical_release_signature(
        self,
        key: str,
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[tuple[int, tuple[tuple[str, Optional[int]], ...]]]:
        entries = self._release_track_entries(key, release_examples, discogs_details)
        if not entries:
            return None

        normalized = []
        for title, duration in entries:
            if not title:
                return None
            normalized.append((title, duration))

        return len(normalized), tuple(normalized)

    def _release_track_entries(
        self,
        key: str,
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[list[tuple[str, Optional[int]]]]:
        """Get normalized track entries (title, duration) for a release."""
        provider, release_id = self.split_release_key(key)

        if provider == "musicbrainz":
            release_data = self.musicbrainz.release_tracker.releases.get(release_id)
            if not release_data:
                release_data = self.musicbrainz._fetch_release_tracks(release_id)
                if release_data:
                    self.musicbrainz.release_tracker.releases[release_id] = release_data

            if not release_data or not release_data.tracks:
                return None

            entries: list[tuple[str, Optional[int]]] = []
            for track in release_data.tracks:
                if not track.title:
                    return None
                entries.append(
                    (normalize_match_text(track.title), track.duration_seconds)
                )
            return entries

        if provider == "discogs":
            details = discogs_details.get(key)
            if not details and self.discogs:
                try:
                    details = self.discogs.get_release(int(release_id))
                except (ValueError, TypeError):
                    details = None
                if details:
                    discogs_details[key] = details

            if not details:
                return None

            tracklist = details.get("tracklist") or []
            entries = []
            for track in tracklist:
                if track.get("type_", "track") not in (None, "", "track"):
                    continue
                title = track.get("title")
                if not title:
                    return None
                entries.append(
                    (
                        normalize_match_text(title),
                        parse_discogs_duration(track.get("duration")),
                    )
                )
            return entries or None

        return None

    def _release_home_for_key(
        self,
        release_key: str,
        current_dir: Path,
        current_count: int,
    ) -> tuple[Optional[Path], int]:
        """Get the release home directory and track count for a release key."""
        cached_home = self.cache.get_release_home(release_key)
        if cached_home:
            raw_path, cached_count, cached_hash = cached_home
            candidate = Path(raw_path)
            if candidate.exists() and candidate != current_dir:
                current_hash = self.cache.get_directory_hash(candidate)
                if cached_hash and current_hash and cached_hash != current_hash:
                    self.cache.delete_release_home(release_key)
                else:
                    return candidate, int(
                        cached_count or self._count_audio_files(candidate)
                    )
            elif not candidate.exists():
                self.cache.delete_release_home(release_key)

        provider, plain = self.split_release_key(release_key)
        if provider != "musicbrainz":
            return None, 0

        fallback = self.find_release_home(plain, current_dir, current_count)
        if not fallback:
            return None, 0

        return fallback, self._count_audio_files(fallback)

    def _count_audio_files(self, directory: Path) -> int:
        """Count audio files in a directory."""
        if not directory.exists():
            return 0
        if self._count_audio_files_fn:
            return self._count_audio_files_fn(directory)
        return 0

    def match_pending_to_release(
        self, meta: TrackMetadata, release_data
    ) -> Optional[float]:
        """
        Calculate best match score between a track and a release.

        Returns the highest combined title + duration similarity score
        across all tracks in the release.
        """
        from ..heuristics import guess_metadata_from_path
        from ..match_utils import combine_similarity, duration_similarity, title_similarity

        title = meta.title or guess_metadata_from_path(meta.path).title
        duration = meta.duration_seconds
        if duration is None:
            duration = self.musicbrainz._probe_duration(meta.path)
            if duration:
                meta.duration_seconds = duration

        best = 0.0
        for track in release_data.tracks:
            combined = combine_similarity(
                title_similarity(title, track.title),
                duration_similarity(duration, track.duration_seconds),
            )
            if combined is not None and combined > best:
                best = combined

        if best <= 0.0:
            return None
        return best
