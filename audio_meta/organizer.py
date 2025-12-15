from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional, Set

from .config import LibrarySettings, OrganizerSettings
from .models import TrackMetadata

logger = logging.getLogger(__name__)

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


class Organizer:
    def __init__(self, settings: OrganizerSettings, library_settings: LibrarySettings) -> None:
        self.settings = settings
        self.enabled = settings.enabled
        default_root = library_settings.roots[0] if library_settings.roots else Path.cwd()
        self.target_root = (settings.target_root or default_root).resolve()
        self.release_composers: Dict[str, Set[str]] = defaultdict(set)

    def plan_target(self, meta: TrackMetadata, is_classical: bool) -> Optional[Path]:
        if not self.enabled:
            return None
        target_dir = self._build_directory(meta, is_classical)
        if not target_dir:
            return None
        target = target_dir / meta.path.name
        if target == meta.path:
            return None
        return target

    def move(self, meta: TrackMetadata, target: Path, dry_run: bool = False) -> None:
        if not target or not self.enabled:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            logger.info("Dry-run would move %s -> %s", meta.path, target)
            return
        try:
            meta.path.rename(target)
            logger.info("Moved %s -> %s", meta.path, target)
            meta.path = target
        except OSError as exc:
            logger.warning("Failed to move %s -> %s: %s", meta.path, target, exc)

    def _build_directory(self, meta: TrackMetadata, is_classical: bool) -> Optional[Path]:
        if is_classical:
            return self._classical_directory(meta)
        artist = self._safe(meta.album_artist or meta.artist, UNKNOWN_ARTIST)
        album = self._safe(meta.album, UNKNOWN_ALBUM)
        return self.target_root / artist / album

    def _classical_directory(self, meta: TrackMetadata) -> Optional[Path]:
        composer = self._safe(meta.composer, UNKNOWN_ARTIST)
        performer = self._safe(meta.album_artist or meta.artist, UNKNOWN_ARTIST)
        album = self._safe(meta.album, UNKNOWN_ALBUM)
        if composer:
            release_key = self._release_key(meta)
            composers = self.release_composers[release_key]
            composers.add(composer)
            if self.settings.classical_mixed_strategy == "performer_album" and len(composers) > 1:
                return self.target_root / performer / album
            return self.target_root / composer / performer / album
        return self.target_root / performer / album

    def _release_key(self, meta: TrackMetadata) -> str:
        if meta.musicbrainz_release_id:
            return meta.musicbrainz_release_id
        album = self._safe(meta.album, UNKNOWN_ALBUM)
        artist = self._safe(meta.album_artist or meta.artist, UNKNOWN_ARTIST)
        return f"{artist}|{album}"

    @staticmethod
    def _safe(value: Optional[str], fallback: str) -> str:
        if not value:
            return fallback
        cleaned = value.strip()
        cleaned = re.sub(r"[\\/]+", "-", cleaned)
        return cleaned or fallback
