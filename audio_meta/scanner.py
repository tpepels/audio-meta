from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from collections.abc import Iterator
from pathlib import Path

from .config import LibrarySettings
from .models import TrackMetadata


@dataclass
class DirectoryBatch:
    directory: Path
    files: list[Path]


class LibraryScanner:
    """Walks the filesystem and yields TrackMetadata placeholders that will be enriched later."""

    def __init__(self, settings: LibrarySettings) -> None:
        self.settings = settings
        self._exts = {ext.lower() for ext in self.settings.include_extensions}

    def iter_tracks(self) -> Iterator[TrackMetadata]:
        for root in self.settings.roots:
            if not root.exists():
                continue
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                if not self._should_include(file_path):
                    continue
                yield TrackMetadata(path=file_path)

    def _should_include(self, path: Path) -> bool:
        if path.suffix.lower() not in self._exts:
            return False
        rel = str(path)
        for pattern in self.settings.exclude_patterns:
            if fnmatch.fnmatch(rel, pattern):
                return False
        return True

    def iter_directories(self) -> Iterator[DirectoryBatch]:
        for root in self.settings.roots:
            if not root.exists():
                continue
            for dirpath, _, filenames in os.walk(root):
                directory = Path(dirpath)
                files: list[Path] = []
                for name in filenames:
                    file_path = directory / name
                    if not file_path.is_file():
                        continue
                    if not self._should_include(file_path):
                        continue
                    files.append(file_path)
                if files:
                    yield DirectoryBatch(directory=directory, files=files)

    def collect_directory(self, directory: Path) -> DirectoryBatch | None:
        if not directory.exists() or not directory.is_dir():
            return None
        files = [
            path
            for path in directory.iterdir()
            if path.is_file() and self._should_include(path)
        ]
        if not files:
            return None
        return DirectoryBatch(directory=directory, files=files)
