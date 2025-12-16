from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from .classical import ClassicalHeuristics
from .config import Settings
from .heuristics import guess_metadata_from_path
from .models import TrackMetadata
from .organizer import Organizer
from .tagging import TagWriter

logger = logging.getLogger(__name__)


class LibraryAuditor:
    """Detects and optionally fixes files that are stored under the wrong artist/album."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.organizer = Organizer(settings.organizer, settings.library)
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
