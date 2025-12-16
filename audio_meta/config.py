from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class LibrarySettings(BaseModel):
    roots: List[Path]
    include_extensions: List[str] = Field(default_factory=lambda: [".mp3", ".flac", ".m4a", ".ogg"])
    exclude_patterns: List[str] = Field(default_factory=list)

    @field_validator("roots", mode="before")
    @classmethod
    def _expand_roots(cls, values: List[str]) -> List[Path]:
        return [Path(v).expanduser().resolve() for v in values]


class ClassicalSettings(BaseModel):
    genre_keywords: List[str] = Field(default_factory=lambda: ["classical", "baroque", "romantic"])
    title_markers: List[str] = Field(default_factory=lambda: ["symphony", "concerto", "sonata", "suite"])
    min_duration_seconds: int = 540


class ProviderSettings(BaseModel):
    acoustid_api_key: str
    musicbrainz_useragent: str = "audio-meta/0.1 (unknown@example.com)"
    discogs_token: Optional[str] = None
    discogs_useragent: str = "audio-meta/0.1 +https://example.com"


class DaemonSettings(BaseModel):
    worker_concurrency: int = 4
    cache_path: Path = Path("./cache/cache.sqlite3")

    @field_validator("cache_path", mode="before")
    @classmethod
    def _expand_cache(cls, value: str | Path) -> Path:
        return Path(value).expanduser().resolve()


class OrganizerSettings(BaseModel):
    enabled: bool = False
    target_root: Optional[Path] = None
    classical_mixed_strategy: str = "performer_album"
    cleanup_empty_dirs: bool = False
    max_filename_length: int = 255
    archive_root: Optional[Path] = None

    @field_validator("target_root", mode="before")
    @classmethod
    def _expand_target(cls, value: Optional[str | Path]) -> Optional[Path]:
        if value is None:
            return None
        return Path(value).expanduser().resolve()

    @field_validator("archive_root", mode="before")
    @classmethod
    def _expand_archive(cls, value: Optional[str | Path]) -> Optional[Path]:
        if value is None:
            return None
        return Path(value).expanduser().resolve()


class Settings(BaseModel):
    library: LibrarySettings
    providers: ProviderSettings
    classical: ClassicalSettings = ClassicalSettings()
    daemon: DaemonSettings = DaemonSettings()
    organizer: OrganizerSettings = OrganizerSettings()

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls.model_validate(raw)


def find_config(explicit_path: Optional[Path]) -> Path:
    if explicit_path:
        return explicit_path
    cwd = Path.cwd()
    for candidate in (cwd / "config.yaml", cwd / "config.yml"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Could not find config.yaml – pass --config explicitly.")
