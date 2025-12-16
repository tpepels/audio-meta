from __future__ import annotations

import logging
import os
from dataclasses import dataclass
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

    def __init__(self, settings: Settings, cache: Optional[MetadataCache] = None) -> None:
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
                    classical = self.heuristics.evaluate(meta).is_classical
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

        assign("album_artist", "album_artist", "albumartist", "artist")
        assign("artist", "artist")
        assign("album", "album")
        assign("title", "title")
        assign("composer", "composer")
        assign("genre", "genre")
        assign("work", "work")
        assign("movement", "movement")

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
        target_map: Dict[str, list[tuple[Path, int]]] = defaultdict(list)
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
                classical = self.heuristics.evaluate(meta).is_classical
                canonical = self.organizer.canonical_target(meta, classical)
                key = str(canonical.parent) if canonical else None
                if key:
                    target_map[key].append((directory, len(audio_files)))
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
                pending.append(
                    {
                        "directory": directory,
                        "file_path": file_path,
                        "meta": meta,
                        "classical": classical,
                        "canonical": canonical,
                        "release_id": release_id,
                        "key": key,
                    }
                )
        for record in pending:
            directory = record["directory"]
            file_path = record["file_path"]
            meta = record["meta"]
            canonical = record["canonical"]
            release_id = record["release_id"]
            key = record["key"]
            target: Optional[Path] = None
            release_home = self._find_release_home(release_id, directory)
            if canonical and canonical != file_path:
                target = canonical
            elif release_home and release_home != directory:
                filename = canonical.name if canonical else file_path.name
                target = self.organizer._truncate_target(release_home / filename)
            elif key:
                release_home = self._best_directory_for_key(key, directory, target_map)
                if release_home and release_home != directory:
                    filename = canonical.name if canonical else file_path.name
                    target = self.organizer._truncate_target(release_home / filename)
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
