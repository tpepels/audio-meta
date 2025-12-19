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

from .cache import MetadataCache
from .config import LibrarySettings, OrganizerSettings
from .heuristics import guess_metadata_from_path
from .models import TrackMetadata

logger = logging.getLogger(__name__)

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


class Organizer:
    def __init__(
        self,
        settings: OrganizerSettings,
        library_settings: LibrarySettings,
        cache: Optional[MetadataCache] = None,
    ) -> None:
        self.settings = settings
        self.enabled = settings.enabled
        default_root = (
            library_settings.roots[0] if library_settings.roots else Path.cwd()
        )
        self.target_root = (settings.target_root or default_root).resolve()
        self.release_composers: Dict[str, Set[str]] = defaultdict(set)
        self.library_roots = [root.resolve() for root in library_settings.roots]
        self.audio_extensions = {
            ext.lower() for ext in library_settings.include_extensions
        }
        self.cache = cache
        self._layout_cache: Dict[str, str] = {}
        self._unknown_labels = {UNKNOWN_ARTIST, UNKNOWN_ALBUM}

    def canonical_target(
        self, meta: TrackMetadata, is_classical: bool
    ) -> Optional[Path]:
        if not self.enabled:
            return None
        # Ensure people fields have stable spelling so that both tags and directories
        # converge on the same canonical representation across the library.
        self.canonicalize_people_fields(meta)
        target_dir = self._build_directory(meta, is_classical)
        if not target_dir:
            return None
        target_filename = self._build_filename(meta)
        return self._truncate_target(target_dir / target_filename)

    def plan_target(self, meta: TrackMetadata, is_classical: bool) -> Optional[Path]:
        target = self.canonical_target(meta, is_classical)
        if not target:
            return None
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
        trackno: Optional[int] = meta.track_number
        guess = guess_metadata_from_path(meta.path)
        if guess.track_number and not trackno:
            trackno = guess.track_number
        base_title = self._safe(title or guess.title or meta.path.stem, "Unknown Title")
        if trackno:
            return f"{trackno:02d} - {base_title}{meta.path.suffix}"
        return f"{base_title}{meta.path.suffix}"

    def _truncate_target(self, target: Path) -> Path:
        max_length = self.settings.max_filename_length or 255
        name = target.name
        if len(name.encode("utf-8")) <= max_length:
            return target
        stem = target.stem
        suffix = target.suffix
        ellipsis = "…"
        allowed = max_length - len(suffix.encode("utf-8"))
        if allowed <= len(ellipsis.encode("utf-8")):
            truncated = ellipsis + suffix
            return target.with_name(truncated)
        encoded = stem.encode("utf-8")
        truncated_bytes = encoded[: allowed - len(ellipsis.encode("utf-8"))]
        truncated_stem = truncated_bytes.decode("utf-8", errors="ignore")
        truncated = f"{truncated_stem}{ellipsis}{suffix}"
        return target.with_name(truncated)

    def _build_directory(
        self, meta: TrackMetadata, is_classical: bool
    ) -> Optional[Path]:
        if is_classical:
            return self._classical_directory(meta)
        artist = self._safe(self._primary_artist(meta), UNKNOWN_ARTIST)
        album = self._safe(meta.album or self._guess_album(meta), UNKNOWN_ALBUM)
        return self._build_path([("artist", artist), ("album", album)])

    def _classical_directory(self, meta: TrackMetadata) -> Optional[Path]:
        composer = self._safe(meta.composer, UNKNOWN_ARTIST)
        performer = self._safe(self._primary_artist(meta), UNKNOWN_ARTIST)
        album = self._safe(meta.album or self._guess_album(meta), UNKNOWN_ALBUM)
        layout_key = self._layout_cache_key(meta)
        cached_layout = self._get_cached_layout(layout_key)
        if cached_layout:
            return self._path_for_layout(cached_layout, composer, performer, album)

        layout = self._choose_classical_layout(meta, composer, performer)
        if layout_key:
            self._remember_layout(layout_key, layout)
        return self._path_for_layout(layout, composer, performer, album)

    def _release_key(self, meta: TrackMetadata) -> str:
        if meta.musicbrainz_release_id:
            return meta.musicbrainz_release_id
        album = self._safe(meta.album or self._guess_album(meta), UNKNOWN_ALBUM)
        artist = self._safe(self._primary_artist(meta), UNKNOWN_ARTIST)
        return f"{artist}|{album}"

    def _layout_cache_key(self, meta: TrackMetadata) -> Optional[str]:
        if meta.musicbrainz_release_id:
            return meta.musicbrainz_release_id
        release_key = self._release_key(meta)
        return f"fallback:{release_key}" if release_key else None

    def _composer_tracker_key(self, meta: TrackMetadata) -> str:
        return meta.musicbrainz_release_id or self._release_key(meta)

    def _get_cached_layout(self, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        if key in self._layout_cache:
            return self._layout_cache[key]
        if self.cache:
            layout = self.cache.get_release_layout(key)
            if layout:
                self._layout_cache[key] = layout
                return layout
        return None

    def _remember_layout(self, key: Optional[str], layout: str) -> None:
        if not key:
            return
        self._layout_cache[key] = layout
        if self.cache:
            self.cache.set_release_layout(key, layout)

    def _choose_classical_layout(
        self, meta: TrackMetadata, composer: str, performer: str
    ) -> str:
        if not composer or composer == UNKNOWN_ARTIST:
            return "performer_album"

        strategy = self.settings.classical_mixed_strategy

        if strategy != "performer_album" and composer == performer:
            return "composer_album"

        tracker_key = self._composer_tracker_key(meta)
        composers = self.release_composers[tracker_key]
        composers.add(composer)
        if strategy == "performer_album" and len(composers) > 1:
            return "performer_album"
        if composer == performer:
            return "composer_album"
        return "composer_performer_album"

    def _path_for_layout(
        self, layout: str, composer: str, performer: str, album: str
    ) -> Path:
        if layout == "composer_album":
            segments = [("composer", composer), ("album", album)]
        elif layout == "composer_performer_album":
            segments = [
                ("composer", composer),
                ("performer", performer),
                ("album", album),
            ]
        else:
            segments = [("performer", performer), ("album", album)]
        return self._build_path(segments)

    def _build_path(self, segments: list[tuple[str, str]]) -> Path:
        path = self.target_root
        for label_type, raw_value in segments:
            fallback = UNKNOWN_ALBUM if label_type == "album" else UNKNOWN_ARTIST
            value = raw_value or fallback
            canonical = self._canonicalize_label(value, label_type, path)
            path = path / canonical
        return path

    @staticmethod
    def _safe(value: Optional[str], fallback: str) -> str:
        if not value:
            return fallback
        cleaned = value.strip()
        cleaned = (
            unicodedata.normalize("NFKD", cleaned)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
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

        # Split on semicolons and commas
        parts = [part.strip() for part in re.split(r"[;,]+", source) if part.strip()]

        if not parts:
            return source

        # If there's only one part, use it
        if len(parts) == 1:
            return parts[0]

        # For multiple artists, prefer groups/ensembles over individuals
        # Common ensemble indicators (case-insensitive)
        ensemble_keywords = {
            'quartet', 'quintet', 'sextet', 'septet', 'octet',
            'trio', 'duo', 'ensemble', 'orchestra', 'philharmonic',
            'symphony', 'chamber', 'band', 'choir', 'chorus',
            'consort', 'collective', 'group'
        }

        for part in parts:
            part_lower = part.lower()
            if any(keyword in part_lower for keyword in ensemble_keywords):
                return part

        # If no ensemble found, prefer the last artist (often the main performer)
        # This handles cases like "Composer, Main Performer"
        return parts[-1]

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

    def _canonicalize_label(self, value: str, label_type: str, parent: Path) -> str:
        if value in self._unknown_labels:
            return value
        if label_type in {"composer", "performer"}:
            value = self._canonicalize_person_name(value)
        normalized = self._normalize_token(value)
        if not normalized:
            return value
        token = self._canonical_token(label_type, parent, normalized)
        cached = (
            self.cache.get_canonical_name(token) if (self.cache and token) else None
        )
        if cached:
            return cached

        # Try identity scanner format (category::normalized)
        identity_token = f"{label_type}::{normalized}"
        cached = (
            self.cache.get_canonical_name(identity_token) if self.cache else None
        )
        if cached:
            return cached
        existing = self._find_existing_label(parent, normalized)
        canonical = existing or value
        if token and self.cache and canonical not in self._unknown_labels:
            self.cache.set_canonical_name(token, canonical)
        return canonical

    def prime_canonical_people(self, *, composers: list[str], performers: list[str]) -> None:
        if not self.cache:
            return
        parent = self.target_root
        grouped: dict[str, list[str]] = {}

        def add(label_type: str, raw: str) -> None:
            for token_value in self._split_people(raw):
                candidate = self._canonicalize_person_name(token_value)
                normalized = self._normalize_token(candidate)
                if not normalized:
                    continue
                token = self._canonical_token(label_type, parent, normalized)
                grouped.setdefault(token, []).append(candidate)

        for value in composers:
            if value:
                add("composer", value)
        for value in performers:
            if value:
                add("performer", value)

        for token, seen in grouped.items():
            best = self._choose_best_person_label(seen)
            if best and best not in self._unknown_labels:
                self.cache.set_canonical_name(token, best)

    def canonicalize_people_fields(self, meta: TrackMetadata) -> None:
        meta.composer = self._canonicalize_people_string(meta.composer, "composer")
        meta.album_artist = self._canonicalize_people_string(
            meta.album_artist, "performer"
        )
        meta.artist = self._canonicalize_people_string(meta.artist, "performer")
        if meta.conductor:
            meta.conductor = self._canonicalize_people_string(
                meta.conductor, "performer"
            )
        if meta.performers:
            canonical = []
            for entry in meta.performers:
                updated = self._canonicalize_people_string(entry, "performer")
                if updated:
                    canonical.extend(self._split_people(updated))
            meta.performers = canonical or meta.performers

    def _canonicalize_people_string(
        self, value: Optional[str], label_type: str
    ) -> Optional[str]:
        if not value:
            return value
        parts = []
        for token_value in self._split_people(value):
            token_value = self._canonicalize_person_name(token_value)
            canonical = self._canonicalize_label(token_value, label_type, self.target_root)
            parts.append(canonical)
        parts = [p for p in parts if p and p not in self._unknown_labels]
        if not parts:
            return value
        unique: list[str] = []
        for p in parts:
            if p not in unique:
                unique.append(p)
        return "; ".join(unique)

    @staticmethod
    def _split_people(value: str) -> list[str]:
        if not value:
            return []
        normalized = value.replace(" / ", ";").replace("/", ";")
        raw = [chunk.strip() for chunk in normalized.split(";") if chunk.strip()]
        return raw

    @staticmethod
    def _canonicalize_person_name(value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            return value
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\s+\(\d+\)\s*$", "", cleaned).strip()
        if ";" in cleaned:
            return cleaned
        if cleaned.count(",") == 1:
            left, right = [part.strip() for part in cleaned.split(",", 1)]
            if left and right and " " not in left:
                return f"{right} {left}".strip()
        return cleaned

    @staticmethod
    def _choose_best_person_label(choices: list[str]) -> Optional[str]:
        cleaned = [c.strip() for c in choices if isinstance(c, str) and c.strip()]
        if not cleaned:
            return None

        def score(value: str) -> tuple[int, int, int, int, str]:
            has_comma = 1 if "," in value else 0
            all_caps = 1 if value.isupper() else 0
            word_count = len([p for p in value.split(" ") if p])
            length = len(value)
            return (has_comma, all_caps, -word_count, -length, value.casefold())

        cleaned.sort(key=score)
        return cleaned[0]

    def _canonical_token(
        self, label_type: str, parent: Path, normalized_value: str
    ) -> str:
        parent_token = ""
        try:
            rel = parent.relative_to(self.target_root)
            parent_token = self._normalize_token(str(rel))
        except ValueError:
            parent_token = self._normalize_token(str(parent))
        return f"{label_type}:{parent_token}:{normalized_value}"

    def _find_existing_label(
        self, parent: Path, normalized_value: str
    ) -> Optional[str]:
        try:
            if not parent.exists():
                return None
        except FileNotFoundError:
            return None
        try:
            for child in parent.iterdir():
                try:
                    if (
                        child.is_dir()
                        and self._normalize_token(child.name) == normalized_value
                    ):
                        return child.name
                except OSError:
                    continue
        except OSError:
            return None
        return None

    @staticmethod
    def _normalize_token(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_only = ascii_only.lower()
        ascii_only = re.sub(r"[^a-z0-9]+", " ", ascii_only)
        ascii_only = re.sub(r"\s+", "", ascii_only)
        return ascii_only
