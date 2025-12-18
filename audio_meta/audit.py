from __future__ import annotations

import logging
import os
from dataclasses import dataclass
import unicodedata
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from .cache import MetadataCache
from .classical import ClassicalHeuristics
from .config import Settings
from .heuristics import guess_metadata_from_path
from .models import TrackMetadata
from .organizer import Organizer
from .tagging import TagWriter
from .providers.musicbrainz import MusicBrainzClient

logger = logging.getLogger(__name__)


@dataclass
class SingletonEntry:
    directory: Path
    file_path: Path
    meta: TrackMetadata
    is_classical: bool
    target: Optional[Path]
    canonical_path: Optional[Path]
    release_home: Optional[Path]
    release_id: Optional[str]


class LibraryAuditor:
    """Detects and optionally fixes files that are stored under the wrong artist/album."""

    def __init__(
        self,
        settings: Settings,
        cache: Optional[MetadataCache] = None,
        musicbrainz: Optional[MusicBrainzClient] = None,
    ) -> None:
        self.settings = settings
        self._cache = cache
        self._owns_cache = False
        if self._cache is None:
            self._cache = MetadataCache(settings.daemon.cache_path)
            self._owns_cache = True
        self.cache = self._cache
        self.organizer = Organizer(settings.organizer, settings.library, cache=self.cache)
        # We always want to be able to compute destinations even if organizer is disabled globally.
        self.organizer.enabled = True
        self.heuristics = ClassicalHeuristics(settings.classical)
        self.tag_writer = TagWriter()
        self.musicbrainz = musicbrainz
        if self.musicbrainz is None:
            try:
                self.musicbrainz = MusicBrainzClient(settings.providers, cache=self.cache)
            except Exception as exc:  # pragma: no cover - provider initialization errors are logged
                logger.warning("MusicBrainz lookups disabled for singleton repair: %s", exc)
        self.extensions = {ext.lower() for ext in settings.library.include_extensions}
        self.library_roots = [root.resolve() for root in settings.library.roots]

    def run(self, fix: bool = False) -> None:
        mismatches: Dict[Path, List[tuple[Path, Path]]] = defaultdict(list)
        singletons: List[Path] = []
        checked_files = 0
        fixed = 0
        for root in self.library_roots:
            if not root.exists():
                continue
            for dirpath, _, filenames in os.walk(root):
                directory = Path(dirpath)
                audio_files = [
                    directory / name
                    for name in filenames
                    if Path(name).suffix.lower() in self.extensions
                ]
                if not audio_files:
                    continue
                if len(audio_files) == 1:
                    singletons.append(directory)
                for path in audio_files:
                    checked_files += 1
                    if not path.exists():
                        continue
                    meta = TrackMetadata(path=path)
                    tags = self.tag_writer.read_existing_tags(meta) or {}
                    self._apply_tag_values(meta, tags)
                    classical = self.heuristics.adapt_metadata(meta)
                    target = self.organizer.plan_target(meta, classical)
                    if not target:
                        continue
                    try:
                        current = path.resolve()
                        expected = target.resolve()
                    except FileNotFoundError:
                        continue
                    if current == expected:
                        continue
                    mismatches[directory].append((current, target))
                    if fix:
                        self.organizer.move(meta, target, dry_run=False)
                        self.organizer.cleanup_source_directory(current.parent)
                        fixed += 1
        self._report(mismatches, singletons, checked_files, fixed, fix)
        if self._owns_cache and self._cache:
            self._cache.close()

    def _apply_tag_values(self, meta: TrackMetadata, tags: dict[str, Optional[str]]) -> None:
        guess = guess_metadata_from_path(meta.path)

        def assign(attr: str, *keys: str) -> None:
            if getattr(meta, attr, None):
                return
            for key in keys:
                value = tags.get(key)
                if value:
                    setattr(meta, attr, value.strip())
                    return
            fallback = getattr(guess, attr, None)
            if fallback:
                setattr(meta, attr, fallback)
        def assign_list(attr: str, *keys: str) -> None:
            if getattr(meta, attr, None):
                return
            for key in keys:
                value = tags.get(key)
                if not value:
                    continue
                parts = [chunk.strip() for chunk in value.replace(" / ", ";").split(";")]
                cleaned = [p for p in parts if p]
                if cleaned:
                    setattr(meta, attr, cleaned)
                    return

        assign("album_artist", "album_artist", "albumartist", "artist")
        assign("artist", "artist")
        assign("album", "album")
        assign("title", "title")
        assign("composer", "composer")
        assign("genre", "genre")
        assign("work", "work")
        assign("movement", "movement")
        assign("conductor", "conductor")
        assign_list("performers", "performers", "performer")

        track_number = tags.get("tracknumber") or tags.get("track_number")
        if track_number:
            cleaned = track_number.strip()
            if "/" in cleaned:
                cleaned = cleaned.split("/", 1)[0]
            if cleaned.isdigit():
                meta.extra["TRACKNUMBER"] = int(cleaned)
        elif guess.track_number is not None:
            meta.extra["TRACKNUMBER"] = guess.track_number

    def _report(
        self,
        mismatches: Dict[Path, List[tuple[Path, Path]]],
        singletons: List[Path],
        checked_files: int,
        fixed: int,
        fix: bool,
    ) -> None:
        if not mismatches and not singletons:
            print("Audit complete: no misplaced files detected.")
            return
        print(f"Audited {checked_files} file(s).")
        if mismatches:
            total = sum(len(entries) for entries in mismatches.values())
            print(f"\nDetected {total} file(s) stored under unexpected directories:")
            for directory, entries in sorted(mismatches.items(), key=lambda item: item[0]):
                rel = self._display(directory)
                print(f"- {rel}")
                for current, target in sorted(entries)[:5]:
                    print(f"    {current.name} -> {self._display(target.parent)}/{target.name}")
                remaining = len(entries) - min(len(entries), 5)
                if remaining > 0:
                    print(f"    … {remaining} more")
        if singletons:
            print(f"\nDirectories containing a single audio file ({len(singletons)}):")
            for path in sorted(singletons)[:10]:
                print(f" - {self._display(path)}")
            remaining = len(singletons) - min(len(singletons), 10)
            if remaining > 0:
                print(f"   … {remaining} more")
        if fix:
            print(f"\nAuto-fixed {fixed} misplaced file(s).")
        else:
            print("\nRe-run with `--fix` to move the mismatched files automatically.")

    def _display(self, path: Path) -> str:
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            resolved = path
        for root in self.library_roots:
            try:
                return str(resolved.relative_to(root))
            except ValueError:
                continue
        return str(path)

    def display_path(self, path: Path) -> str:
        return self._display(path)

    def collect_singletons(self) -> List[SingletonEntry]:
        entries: List[SingletonEntry] = []
        group_map: Dict[tuple[str, str, str], list[tuple[Path, int]]] = defaultdict(list)
        pending: list[dict] = []
        for root in self.library_roots:
            if not root.exists():
                continue
            for dirpath, _, filenames in os.walk(root):
                directory = Path(dirpath)
                audio_files = [
                    directory / name
                    for name in filenames
                    if Path(name).suffix.lower() in self.extensions
                ]
                if not audio_files:
                    continue
                file_path = audio_files[0]
                meta = TrackMetadata(path=file_path)
                tags = self.tag_writer.read_existing_tags(meta) or {}
                self._apply_tag_values(meta, tags)
                classical = self.heuristics.adapt_metadata(meta)
                canonical = self.organizer.canonical_target(meta, classical)
                group_key = self._group_key(meta, classical)
                if group_key:
                    group_map[group_key].append((directory, len(audio_files)))
                if len(audio_files) != 1:
                    continue
                if self.cache and self.cache.is_directory_ignored(directory):
                    continue
                release_entry = self.cache.get_directory_release(directory) if self.cache else None
                release_id: Optional[str] = None
                if release_entry:
                    _, release_id, _ = release_entry
                if not release_id and meta.musicbrainz_release_id:
                    release_id = meta.musicbrainz_release_id
                if not release_id:
                    release_id = self._ensure_release_id(directory, meta)
                pending.append(
                    {
                        "directory": directory,
                        "file_path": file_path,
                        "meta": meta,
                        "classical": classical,
                        "canonical": canonical,
                        "release_id": release_id,
                        "group_key": group_key,
                    }
                )
        for record in pending:
            directory = record["directory"]
            file_path = record["file_path"]
            meta = record["meta"]
            canonical = record["canonical"]
            release_id = record["release_id"]
            group_key = record["group_key"]
            target: Optional[Path] = None
            release_home = self._find_release_home(release_id, directory)
            if canonical and canonical != file_path:
                target = canonical
            elif release_home and release_home != directory:
                filename = canonical.name if canonical else file_path.name
                target = self.organizer._truncate_target(release_home / filename)
            elif group_key:
                release_home = self._best_directory_for_group(group_key, directory, group_map)
                if release_home and release_home != directory:
                    filename = canonical.name if canonical else file_path.name
                    target = self.organizer._truncate_target(release_home / filename)
                    logger.debug(
                        "Singleton %s matched group %s -> %s",
                        directory,
                        group_key,
                        release_home,
                    )
            entries.append(
                SingletonEntry(
                    directory=directory,
                    file_path=file_path,
                    meta=meta,
                    is_classical=record["classical"],
                    target=target,
                    canonical_path=canonical,
                    release_home=release_home,
                    release_id=release_id,
                )
            )

        return entries

    def _group_key(self, meta: TrackMetadata, classical: bool) -> Optional[tuple[str, str, str]]:
        album_key = self._normalize_token(meta.album)
        artist_source = meta.album_artist or meta.artist
        artist_key = self._normalize_token(artist_source)
        composer_key = self._normalize_token(self._primary_composer(meta.composer) if classical else None)

        if not album_key and not artist_key and not composer_key:
            return None

        return (composer_key, album_key, artist_key)

    @staticmethod
    def _primary_composer(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if ";" not in value and "," not in value:
            return value
        parts = [part.strip() for part in re.split(r"[;,]+", value) if part and part.strip()]
        for part in parts:
            if re.search(r"[A-Za-z]", part):
                return part
        return parts[0] if parts else value

    def _best_directory_for_group(
        self,
        group_key: tuple[str, str, str],
        current: Path,
        group_map: Dict[tuple[str, str, str], list[tuple[Path, int]]],
    ) -> Optional[Path]:
        candidates = [
            entry for entry in group_map.get(group_key, []) if entry[0] != current
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[1], len(item[0].parts), str(item[0])))
        return candidates[0][0]

    def _normalize_token(self, value: Optional[str]) -> str:
        if not value:
            return ""
        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_only = ascii_only.lower()
        ascii_only = re.sub(r"[^a-z0-9]+", " ", ascii_only)
        ascii_only = re.sub(r"\s+", "", ascii_only)
        return ascii_only

    def _ensure_release_id(self, directory: Path, meta: TrackMetadata) -> Optional[str]:
        if not self.musicbrainz:
            return None
        try_meta = TrackMetadata(path=meta.path)
        try:
            result = self.musicbrainz.enrich(try_meta)
        except Exception as exc:  # pragma: no cover
            logger.debug("MusicBrainz lookup failed for singleton %s: %s", meta.path, exc)
            return None
        release_id = try_meta.musicbrainz_release_id
        if not release_id:
            return None
        meta.musicbrainz_release_id = meta.musicbrainz_release_id or release_id
        if self.cache:
            score = (result.score if result else 0.0) or (try_meta.match_confidence or 0.0) or 0.5
            self.cache.set_directory_release(directory, "musicbrainz", release_id, score)
        return release_id

    def _find_release_home(self, release_id: Optional[str], current_dir: Path) -> Optional[Path]:
        if not self.cache or not release_id:
            return None
        directories = self.cache.find_directories_for_release(release_id)
        best_dir: Optional[Path] = None
        best_count = 0
        for raw in directories:
            candidate = Path(raw)
            if candidate == current_dir:
                continue
            if not candidate.exists():
                continue
            count = self._count_audio_files(candidate)
            if count > best_count:
                best_dir = candidate
                best_count = count
        return best_dir

    def _count_audio_files(self, directory: Path) -> int:
        count = 0
        for dirpath, _, filenames in os.walk(directory):
            for name in filenames:
                if Path(name).suffix.lower() in self.extensions:
                    count += 1
        return count

    def _best_directory_for_key(
        self, key: str, current_dir: Path, target_map: Dict[str, list[tuple[Path, int]]]
    ) -> Optional[Path]:
        candidates = target_map.get(key) or []
        best_dir: Optional[Path] = None
        best_count = 0
        for directory, count in candidates:
            if directory == current_dir:
                continue
            if count > best_count:
                best_dir = directory
                best_count = count
        return best_dir
