from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from .daemon_types import PendingResult
from .models import TrackMetadata


@dataclass(frozen=True)
class AudioMetaServices:
    daemon: Any

    def display_path(self, path: Path) -> str:
        return self.daemon._display_path(path)

    def release_key(self, provider: str, release_id: str) -> str:
        return self.daemon._release_key(provider, release_id)

    def split_release_key(self, key: str) -> tuple[str, str]:
        return self.daemon._split_release_key(key)

    def schedule_deferred_directory(self, directory: Path, reason: str) -> None:
        self.daemon._schedule_deferred_directory(directory, reason)

    def record_skip(self, directory: Path, reason: str) -> None:
        self.daemon._record_skip(directory, reason)

    def apply_musicbrainz_release_selection(
        self,
        directory: Path,
        release_id: str,
        pending_results: list[PendingResult],
        *,
        force: bool = False,
    ) -> bool:
        return bool(
            self.daemon._apply_musicbrainz_release_selection(
                directory, release_id, pending_results, force=force
            )
        )

    def fetch_musicbrainz_release(self, release_id: str) -> Optional[Any]:
        release_ref = self.daemon.musicbrainz.release_tracker.releases.get(release_id)
        if release_ref:
            return release_ref
        release_ref = self.daemon.musicbrainz._fetch_release_tracks(release_id)
        if release_ref:
            self.daemon.musicbrainz.release_tracker.releases[release_id] = release_ref
        return release_ref

    def apply_discogs_release_details(
        self, pending_results: list[PendingResult], details: dict
    ) -> None:
        self.daemon._apply_discogs_release_details(pending_results, details)

    def discogs_release_artist(self, details: dict) -> Optional[str]:
        return self.daemon._discogs_release_artist(details)

    def discogs_candidates(self, meta: TrackMetadata) -> list[dict]:
        return list(self.daemon._discogs_candidates(meta))

    def persist_directory_release(
        self,
        directory: Path,
        provider: str,
        release_id: str,
        score: float,
        *,
        artist_hint: Optional[str] = None,
        album_hint: Optional[str] = None,
    ) -> None:
        self.daemon._persist_directory_release(
            directory,
            provider,
            release_id,
            score,
            artist_hint=artist_hint,
            album_hint=album_hint,
        )

    def print_release_selection_summary(
        self,
        directory: Path,
        provider: str,
        release_id: str,
        album: Optional[str],
        artist: Optional[str],
        track_count: Optional[int],
        disc_count: Optional[int],
        pending_results: list[PendingResult],
    ) -> None:
        self.daemon._print_release_selection_summary(
            directory,
            provider,
            release_id,
            album,
            artist,
            track_count,
            disc_count,
            pending_results,
        )

    def prompt_on_unmatched_release(
        self, directory: Path, release_key: str, unmatched: list[PendingResult]
    ) -> bool:
        return bool(
            self.daemon._prompt_on_unmatched_release(directory, release_key, unmatched)
        )

    def log_unmatched_candidates(
        self, directory: Path, release_key: str, unmatched: list[PendingResult]
    ) -> None:
        self.daemon._log_unmatched_candidates(directory, release_key, unmatched)
