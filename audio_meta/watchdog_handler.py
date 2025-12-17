from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Iterable

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from .scanner import DirectoryBatch, LibraryScanner

logger = logging.getLogger(__name__)


class WatchHandler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue[DirectoryBatch], exts: Iterable[str], scanner: LibraryScanner) -> None:
        super().__init__()
        self.queue = queue
        self.exts = {ext.lower() for ext in exts}
        self.scanner = scanner

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def _maybe_enqueue(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() in self.exts:
            batch = self.scanner.collect_directory(path.parent)
            if batch:
                logger.debug("Queued directory change: %s", batch.directory)
                asyncio.get_event_loop().call_soon_threadsafe(self.queue.put_nowait, batch)

