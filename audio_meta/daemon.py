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
from .providers.discogs import DiscogsClient
from .providers.musicbrainz import LookupResult, MusicBrainzClient
from .scanner import LibraryScanner
from .tagging import TagWriter
from .cache import MetadataCache

logger = logging.getLogger(__name__)


class DryRunRecorder:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self._lock = Lock()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")

    def record(
        self,
        meta: TrackMetadata,
        score: Optional[float],
        tag_changes: Optional[dict] = None,
        relocate_from: Optional[Path] = None,
        relocate_to: Optional[Path] = None,
    ) -> None:
        payload = meta.to_record()
        payload["match_score"] = score
        if tag_changes:
            payload["tag_changes"] = tag_changes
        if relocate_to:
            payload["relocate_from"] = str(relocate_from or meta.path)
            payload["relocate_to"] = str(relocate_to)
        line = json.dumps(payload, indent=2, sort_keys=True)
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
        self.cache = MetadataCache(settings.daemon.cache_path)
        self.scanner = LibraryScanner(settings.library)
        self.musicbrainz = MusicBrainzClient(settings.providers, cache=self.cache)
        self.discogs = None
        if settings.providers.discogs_token:
            try:
                self.discogs = DiscogsClient(settings.providers, cache=self.cache)
            except Exception as exc:
                logger.warning("Failed to initialise Discogs client: %s", exc)
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
        if not self.dry_run_recorder:
            stat_before = self._safe_stat(path)
            if stat_before:
                cached_state = self.cache.get_processed_file(path)
                if cached_state and cached_state == (stat_before.st_mtime_ns, stat_before.st_size):
                    logger.info("Skipping %s; already processed and unchanged", path)
                    return
        result = self.musicbrainz.enrich(meta)
        if result and self.discogs and self._needs_supplement(meta):
            try:
                supplement = self.discogs.supplement(meta)
                if supplement:
                    result = LookupResult(meta, score=max(result.score, supplement.score))
            except Exception:
                logger.exception("Discogs supplement failed for %s", path)
        if not result and self.discogs:
            try:
                result = self.discogs.enrich(meta)
            except Exception:
                logger.exception("Discogs lookup failed for %s", path)
        if not result:
            logger.info("No metadata match for %s", path)
            return
        is_classical = self.heuristics.adapt_metadata(meta)
        tag_changes = self.tag_writer.diff(meta)
        needs_tags = bool(tag_changes)
        target_path = self.organizer.plan_target(meta, is_classical)
        if self.dry_run_recorder:
            if needs_tags or target_path:
                relocate_from = meta.path if target_path else None
                self.dry_run_recorder.record(
                    meta,
                    result.score,
                    tag_changes=tag_changes or None,
                    relocate_from=relocate_from,
                    relocate_to=target_path,
                )
                logger.info("Dry-run recorded planned update for %s", path)
                if target_path:
                    self.organizer.move(meta, target_path, dry_run=True)
            else:
                logger.info("No changes required for %s", path)
            return
        processing_done = False
        try:
            if needs_tags:
                self.tag_writer.apply(meta)
                logger.info("Updated tags for %s", path)
            else:
                logger.info("Tags already up to date for %s", path)
            if target_path:
                self.organizer.move(meta, target_path, dry_run=False)
            processing_done = True
        except ProcessingError as exc:
            logger.warning("Failed to update tags for %s: %s", path, exc)
        if processing_done and not self.dry_run_recorder:
            stat_after = self._safe_stat(meta.path)
            if stat_after:
                self.cache.set_processed_file(meta.path, stat_after.st_mtime_ns, stat_after.st_size)

    def _needs_supplement(self, meta: TrackMetadata) -> bool:
        return not meta.album or not meta.artist or not meta.album_artist

    @staticmethod
    def _safe_stat(path: Path):
        try:
            return path.stat()
        except FileNotFoundError:
            return None
