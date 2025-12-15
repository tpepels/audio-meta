from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path

from .config import LibrarySettings
from .models import TrackMetadata


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
