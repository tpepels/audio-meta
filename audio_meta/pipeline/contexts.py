from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..daemon_types import PendingResult, ReleaseExample
from ..models import TrackMetadata


@dataclass
class DirectoryContext:
    daemon: Any
    directory: Path
    files: list[Path]
    force_prompt: bool
    is_singleton: bool = False
    dir_track_count: int = 0
    dir_year: Optional[int] = None
    directory_hash: Optional[str] = None
    cached_directory_hash: Optional[str] = None
    hash_release_entry: Optional[tuple[str, str, float]] = None

    pending_results: list[PendingResult] = field(default_factory=list)
    release_scores: dict[str, float] = field(default_factory=dict)
    release_examples: dict[str, ReleaseExample] = field(default_factory=dict)
    discogs_details: dict[str, dict] = field(default_factory=dict)

    forced_provider: Optional[str] = None
    forced_release_id: Optional[str] = None
    forced_release_score: float = 0.0
    release_summary_printed: bool = False
    require_release_confirmation: bool = False

    best_release_key: Optional[str] = None
    discogs_release_details: Optional[dict] = None
    unmatched: list[PendingResult] = field(default_factory=list)
    planned: list[Any] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    release_home_dir: Optional[Path] = None
    applied_provider: Optional[str] = None
    applied_release_id: Optional[str] = None
    album_name: str = ""
    album_artist: str = ""
    best_score: float = 0.0

    def set_best_release(self, release_key: Optional[str]) -> None:
        self.best_release_key = release_key

    def split_best_release(self) -> tuple[Optional[str], Optional[str]]:
        if not self.best_release_key:
            return None, None
        if ":" in self.best_release_key:
            provider, rid = self.best_release_key.split(":", 1)
            return provider, rid
        return "musicbrainz", self.best_release_key


@dataclass(frozen=True)
class TrackSignalContext:
    daemon: Any
    directory: Path
    meta: TrackMetadata
    existing_tags: dict[str, Optional[str]]


@dataclass(frozen=True)
class TrackEnrichmentContext:
    daemon: Any
    directory: Path
    meta: TrackMetadata
    existing_tags: dict[str, Optional[str]]


@dataclass(frozen=True)
class PlanApplyContext:
    daemon: Any
    plan: Any


@dataclass(frozen=True)
class TrackSkipContext:
    daemon: Any
    directory: Path
    file_path: Path
    directory_ctx: Any | None = None
