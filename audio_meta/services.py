from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from .daemon_types import PendingResult
from .models import TrackMetadata


@dataclass(frozen=True)
class AudioMetaServices:
    daemon: Any

    @property
    def processing_deferred(self) -> bool:
        return bool(getattr(self.daemon, "_processing_deferred", False))

    def display_path(self, path: Path) -> str:
        return self.daemon._display_path(path)

    def release_key(self, provider: str, release_id: str) -> str:
        return self.daemon._release_key(provider, release_id)

    def split_release_key(self, key: str) -> tuple[str, str]:
        return self.daemon._split_release_key(key)

    def safe_stat(self, path: Path) -> Any:
        return self.daemon._safe_stat(path)

    def directory_context(
        self, directory: Path, files: list[Path]
    ) -> tuple[int, Optional[int]]:
        return self.daemon._directory_context(directory, files)

    def cached_release_for_directory(
        self, directory: Path
    ) -> Optional[tuple[str, str, float]]:
        return self.daemon._cached_release_for_directory(directory)

    def apply_tag_hints(
        self, meta: TrackMetadata, tags: dict[str, Optional[str]]
    ) -> None:
        self.daemon._apply_tag_hints(meta, tags)

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

    def fetch_discogs_release(self, release_id: str) -> Optional[dict]:
        if not getattr(self.daemon, "discogs", None):
            return None
        try:
            return self.daemon.discogs.get_release(int(release_id))
        except Exception:
            return None

    def apply_discogs_release_details(
        self, pending_results: list[PendingResult], details: dict
    ) -> None:
        self.daemon._apply_discogs_release_details(pending_results, details)

    def discogs_release_artist(self, details: dict) -> Optional[str]:
        return self.daemon._discogs_release_artist(details)

    def discogs_candidates(self, meta: TrackMetadata) -> list[dict]:
        return list(self.daemon._discogs_candidates(meta))

    def enrich_track_default(self, meta: TrackMetadata) -> Optional[Any]:
        return self.daemon._enrich_track_default(meta)

    def resolve_unmatched_directory(
        self,
        directory: Path,
        sample_meta: Optional[TrackMetadata],
        dir_track_count: int,
        dir_year: Optional[int],
        *,
        files: Optional[list[Path]] = None,
    ) -> Optional[tuple[str, str]]:
        return self.daemon._resolve_unmatched_directory(
            directory,
            sample_meta,
            dir_track_count,
            dir_year,
            files=files,
        )

    def album_root(self, directory: Path) -> Path:
        return self.daemon._album_root(directory)

    def count_audio_files(self, directory: Path) -> int:
        return int(self.daemon._count_audio_files(directory))

    def maybe_set_release_home(
        self,
        release_key: str,
        directory: Path,
        *,
        track_count: int,
        directory_hash: Optional[str],
    ) -> None:
        self.daemon._maybe_set_release_home(
            release_key,
            directory,
            track_count=track_count,
            directory_hash=directory_hash,
        )

    def path_under_directory(self, path: Path, directory: Path) -> bool:
        return bool(self.daemon._path_under_directory(path, directory))

    def reprocess_directory(self, directory: Path) -> None:
        self.daemon._reprocess_directory(directory)

    def select_singleton_release_home(
        self,
        release_key: str,
        directory: Path,
        current_count: int,
        best_score: float,
        sample_meta: Optional[TrackMetadata],
    ) -> Optional[Path]:
        return self.daemon._select_singleton_release_home(
            release_key, directory, current_count, best_score, sample_meta
        )

    def plan_singleton_target(
        self,
        meta: TrackMetadata,
        release_home_dir: Path,
        is_classical: bool,
    ) -> Optional[Path]:
        return self.daemon._plan_singleton_target(meta, release_home_dir, is_classical)

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
