from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .classical import ClassicalHeuristics
from .config import Settings
from .models import ProcessingError, TrackMetadata
from .organizer import Organizer
from .providers.musicbrainz import MusicBrainzClient
from .scanner import LibraryScanner
from .tagging import TagWriter

logger = logging.getLogger(__name__)


class DryRunRecorder:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self._lock = Lock()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")

    def record(self, meta: TrackMetadata, score: Optional[float], target_path: Optional[Path] = None) -> None:
        payload = meta.to_record()
        payload["match_score"] = score
        if target_path:
            payload["relocate_to"] = str(target_path)
        line = json.dumps(payload)
        with self._lock:
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class _WatchHandler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue[Path], exts: Iterable[str]) -> None:
        super().__init__()
        self.queue = queue
        self.exts = {ext.lower() for ext in exts}

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def _maybe_enqueue(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() in self.exts:
            logger.debug("Queued file change: %s", path)
            asyncio.get_event_loop().call_soon_threadsafe(self.queue.put_nowait, path)


class AudioMetaDaemon:
    def __init__(self, settings: Settings, dry_run_output: Optional[Path] = None) -> None:
        self.settings = settings
        self.scanner = LibraryScanner(settings.library)
        self.musicbrainz = MusicBrainzClient(settings.providers)
        self.heuristics = ClassicalHeuristics(settings.classical)
        self.tag_writer = TagWriter()
        self.organizer = Organizer(settings.organizer, settings.library)
        self.queue: asyncio.Queue[Path] = asyncio.Queue()
        self.observer: Observer | None = None
        self.dry_run_recorder = DryRunRecorder(dry_run_output) if dry_run_output else None
        if self.dry_run_recorder:
            logger.info("Dry-run mode enabled; writing preview to %s", dry_run_output)

    async def run_scan(self) -> None:
        logger.info("Starting one-off scan")
        for track in self.scanner.iter_tracks():
            await self.queue.put(track.path)
        workers = self._start_workers()
        await self.queue.join()
        await self._stop_workers(workers)

    async def run_daemon(self) -> None:
        logger.info("Starting daemon")
        for track in self.scanner.iter_tracks():
            await self.queue.put(track.path)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bootstrap_watchdog)
        workers = self._start_workers()
        try:
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Daemon stopping")
        finally:
            if self.observer:
                self.observer.stop()
                self.observer.join()
            await self._stop_workers(workers)

    def _bootstrap_watchdog(self) -> None:
        handler = _WatchHandler(self.queue, self.settings.library.include_extensions)
        observer = Observer()
        for root in self.settings.library.roots:
            observer.schedule(handler, str(root), recursive=True)
        observer.start()
        self.observer = observer

    def _start_workers(self) -> list[asyncio.Task[None]]:
        return [asyncio.create_task(self._worker(i)) for i in range(self.settings.daemon.worker_concurrency)]

    async def _stop_workers(self, workers: list[asyncio.Task[None]]) -> None:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self, worker_id: int) -> None:
        while True:
            path = await self.queue.get()
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._process_path, path)
            except Exception:  # pragma: no cover - logged and ignored
                logger.exception("Worker %s failed to process %s", worker_id, path)
            finally:
                self.queue.task_done()

    def _process_path(self, path: Path) -> None:
        meta = TrackMetadata(path=path)
        result = self.musicbrainz.enrich(meta)
        if not result:
            logger.info("No metadata match for %s", path)
            return
        is_classical = self.heuristics.adapt_metadata(meta)
        needs_tags = self.tag_writer.has_changes(meta)
        target_path = self.organizer.plan_target(meta, is_classical)
        if self.dry_run_recorder:
            if needs_tags or target_path:
                self.dry_run_recorder.record(meta, result.score, target_path)
                logger.info("Dry-run recorded planned update for %s", path)
                if target_path:
                    self.organizer.move(meta, target_path, dry_run=True)
            else:
                logger.info("No changes required for %s", path)
            return
        try:
            if needs_tags:
                self.tag_writer.apply(meta)
                logger.info("Updated tags for %s", path)
            else:
                logger.info("Tags already up to date for %s", path)
            if target_path:
                self.organizer.move(meta, target_path, dry_run=False)
        except ProcessingError as exc:
            logger.warning("Failed to update tags for %s: %s", path, exc)
