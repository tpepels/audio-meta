from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional, Set

from .config import LibrarySettings, OrganizerSettings
from .heuristics import guess_metadata_from_path
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
        self.library_roots = [root.resolve() for root in library_settings.roots]
        self.audio_extensions = {ext.lower() for ext in library_settings.include_extensions}

    def plan_target(self, meta: TrackMetadata, is_classical: bool) -> Optional[Path]:
        if not self.enabled:
            return None
        target_dir = self._build_directory(meta, is_classical)
        if not target_dir:
            return None
        target_filename = self._build_filename(meta)
        target = target_dir / target_filename
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
            try:
                meta.path.rename(target)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                # Cross-device rename failed; fall back to shutil.move which copies+removes.
                shutil.move(str(meta.path), str(target))
            logger.info("Moved %s -> %s", meta.path, target)
            meta.path = target
        except OSError as exc:
            logger.warning("Failed to move %s -> %s: %s", meta.path, target, exc)

    def cleanup_source_directory(self, directory: Path) -> None:
        if not self.settings.cleanup_empty_dirs:
            return
        current = directory
        while current:
            try:
                resolved = current.resolve()
            except FileNotFoundError:
                resolved = current
            if self._is_library_root(resolved):
                break
            if not self._is_under_library(resolved):
                break
            if not resolved.exists():
                current = resolved.parent
                continue
            if self._directory_has_audio(resolved):
                break
            if not self._remove_tree(resolved):
                break
            logger.info("Removed empty source directory %s", resolved)
            current = resolved.parent
            if not current or current == current.parent:
                break

    def _build_filename(self, meta: TrackMetadata) -> str:
        title = meta.title
        trackno = None
        tags = meta.extra.get("TRACKNUMBER")
        if tags:
            trackno = tags
        guess = guess_metadata_from_path(meta.path)
        if guess.track_number and not trackno:
            trackno = guess.track_number
        base_title = self._safe(title or guess.title or meta.path.stem, "Unknown Title")
        if trackno:
            return f"{trackno:02d} - {base_title}{meta.path.suffix}"
        return f"{base_title}{meta.path.suffix}"

    def _build_directory(self, meta: TrackMetadata, is_classical: bool) -> Optional[Path]:
        if is_classical:
            return self._classical_directory(meta)
        artist = self._safe(self._primary_artist(meta), UNKNOWN_ARTIST)
        album = self._safe(meta.album or self._guess_album(meta), UNKNOWN_ALBUM)
        return self.target_root / artist / album

    def _classical_directory(self, meta: TrackMetadata) -> Optional[Path]:
        composer = self._safe(meta.composer, UNKNOWN_ARTIST)
        performer = self._safe(self._primary_artist(meta), UNKNOWN_ARTIST)
        album = self._safe(meta.album or self._guess_album(meta), UNKNOWN_ALBUM)
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
        album = self._safe(meta.album or self._guess_album(meta), UNKNOWN_ALBUM)
        artist = self._safe(self._primary_artist(meta), UNKNOWN_ARTIST)
        return f"{artist}|{album}"

    @staticmethod
    def _safe(value: Optional[str], fallback: str) -> str:
        if not value:
            return fallback
        cleaned = value.strip()
        cleaned = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[\\/]+", "-", cleaned)
        return cleaned or fallback

    def _guess_album(self, meta: TrackMetadata) -> Optional[str]:
        guess = guess_metadata_from_path(meta.path)
        return guess.album

    def _primary_artist(self, meta: TrackMetadata) -> Optional[str]:
        source = meta.album_artist or meta.artist
        if not source:
            guess = guess_metadata_from_path(meta.path)
            return guess.artist
        parts = [part.strip() for part in re.split(r"[;,]+", source) if part.strip()]
        return parts[0] if parts else source

    def _is_under_library(self, path: Path) -> bool:
        for root in self.library_roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _is_library_root(self, path: Path) -> bool:
        return any(path == root for root in self.library_roots)

    def _directory_has_audio(self, directory: Path) -> bool:
        for root, _, files in os.walk(directory):
            for name in files:
                ext = Path(name).suffix.lower()
                if ext in self.audio_extensions:
                    return True
        return False

    def _remove_tree(self, directory: Path) -> bool:
        try:
            for root, dirs, files in os.walk(directory, topdown=False):
                root_path = Path(root)
                for name in files:
                    try:
                        (root_path / name).unlink()
                    except FileNotFoundError:
                        continue
                for name in dirs:
                    try:
                        (root_path / name).rmdir()
                    except OSError:
                        return False
            directory.rmdir()
            return True
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", directory, exc)
            return False
