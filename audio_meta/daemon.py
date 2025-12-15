from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
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
from .scanner import DirectoryBatch, LibraryScanner
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


@dataclass
class PlannedUpdate:
    meta: TrackMetadata
    score: Optional[float]
    tag_changes: dict
    target_path: Optional[Path]


class _WatchHandler(FileSystemEventHandler):
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
        self.queue: asyncio.Queue[DirectoryBatch] = asyncio.Queue()
        self.observer: Observer | None = None
        self.dry_run_recorder = DryRunRecorder(dry_run_output) if dry_run_output else None
        if self.dry_run_recorder:
            logger.debug("Dry-run mode enabled; writing preview to %s", dry_run_output)

    async def run_scan(self) -> None:
        logger.debug("Starting one-off scan")
        for batch in self.scanner.iter_directories():
            await self.queue.put(batch)
        workers = self._start_workers()
        await self.queue.join()
        await self._stop_workers(workers)

    async def run_daemon(self) -> None:
        logger.debug("Starting daemon")
        for batch in self.scanner.iter_directories():
            await self.queue.put(batch)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._bootstrap_watchdog)
        workers = self._start_workers()
        try:
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.debug("Daemon stopping")
        finally:
            if self.observer:
                self.observer.stop()
                self.observer.join()
            await self._stop_workers(workers)

    def _bootstrap_watchdog(self) -> None:
        handler = _WatchHandler(self.queue, self.settings.library.include_extensions, self.scanner)
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
            batch = await self.queue.get()
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._process_directory, batch)
            except Exception:  # pragma: no cover - logged and ignored
                logger.exception("Worker %s failed to process %s", worker_id, batch.directory)
            finally:
                self.queue.task_done()

    def _process_directory(self, batch: DirectoryBatch) -> None:
        planned: list[PlannedUpdate] = []
        logger.debug("Processing directory %s with %d files", batch.directory, len(batch.files))
        pending_results: list[tuple[TrackMetadata, Optional[LookupResult], bool]] = []
        release_scores: dict[str, float] = {}
        release_examples: dict[str, tuple[str, str]] = {}

        for file_path in batch.files:
            meta = TrackMetadata(path=file_path)
            stat_before = None
            if not self.dry_run_recorder:
                stat_before = self._safe_stat(file_path)
                if stat_before:
                    cached_state = self.cache.get_processed_file(file_path)
                    if cached_state:
                        cached_mtime, cached_size, organized_flag = cached_state
                        if cached_mtime == stat_before.st_mtime_ns and cached_size == stat_before.st_size:
                            if self.organizer.enabled and not organized_flag:
                                logger.debug("Reprocessing %s because organizer is now enabled", file_path)
                            else:
                                moved_target = self.cache.get_move(file_path)
                                if moved_target and Path(moved_target).exists():
                                    logger.warning(
                                        "File %s already moved to %s; skipping stale copy",
                                        file_path,
                                        moved_target,
                                    )
                                    continue
                                logger.debug("Skipping %s; already processed and unchanged", file_path)
                                continue
            result = self.musicbrainz.enrich(meta)
            if result and self.discogs and self._needs_supplement(meta):
                try:
                    supplement = self.discogs.supplement(meta)
                    if supplement:
                        result = LookupResult(meta, score=max(result.score, supplement.score))
                except Exception:
                    logger.exception("Discogs supplement failed for %s", file_path)
            if not result and self.discogs:
                try:
                    result = self.discogs.enrich(meta)
                except Exception:
                    logger.exception("Discogs lookup failed for %s", file_path)
            pending_results.append((meta, result, bool(result)))
            if result and meta.musicbrainz_release_id:
                release_id = meta.musicbrainz_release_id
                release_scores[release_id] = max(release_scores.get(release_id, 0.0), result.score)
                release_examples[release_id] = (
                    meta.album or "",
                    meta.album_artist or meta.artist or "",
                )
        best_release_id = None
        best_score = 0.0
        for rid, score in release_scores.items():
            if score > best_score:
                best_release_id = rid
                best_score = score
        ambiguous_cutoff = 0.05
        if (
            best_release_id
            and sum(1 for score in release_scores.values() if best_score - score <= ambiguous_cutoff) > 1
        ):
            self._warn_ambiguous_release(
                batch.directory,
                [
                    (
                        rid,
                        score,
                        release_examples.get(rid, ("", ""))[0],
                    )
                    for rid, score in release_scores.items()
                    if best_score - score <= ambiguous_cutoff
                ],
            )
            return
        if best_release_id:
            album_name, album_artist = release_examples.get(best_release_id, ("", ""))
        else:
            album_name = album_artist = ""

        for meta, result, matched in pending_results:
            if not matched:
                logger.warning("No metadata match for %s; leaving file untouched", meta.path)
                continue
            if best_release_id and meta.musicbrainz_release_id != best_release_id:
                meta.album = album_name or meta.album
                meta.album_artist = album_artist or meta.album_artist
                meta.musicbrainz_release_id = best_release_id
            is_classical = self.heuristics.adapt_metadata(meta)
            tag_changes = self.tag_writer.diff(meta)
            target_path = self.organizer.plan_target(meta, is_classical)
            if not tag_changes and not target_path:
                logger.debug("No changes required for %s", meta.path)
                continue
            planned.append(
                PlannedUpdate(
                    meta=meta,
                    score=result.score if result else None,
                    tag_changes=tag_changes,
                    target_path=target_path,
                )
            )

        if not planned:
            logger.debug("No actionable files in %s", batch.directory)
            return
        for plan in planned:
            self._apply_plan(plan)

    def _apply_plan(self, plan: PlannedUpdate) -> None:
        meta = plan.meta
        tag_changes = plan.tag_changes
        target_path = plan.target_path
        if self.dry_run_recorder:
            relocate_from = meta.path if target_path else None
            self.dry_run_recorder.record(
                meta,
                plan.score,
                tag_changes=tag_changes or None,
                relocate_from=relocate_from,
                relocate_to=target_path,
            )
            logger.debug("Dry-run recorded planned update for %s", meta.path)
            if target_path:
                self.organizer.move(meta, target_path, dry_run=True)
            return
        original_path = meta.path
        organized_flag = self.organizer.enabled
        try:
            if tag_changes:
                self.tag_writer.apply(meta)
                logger.debug("Updated tags for %s", meta.path)
            else:
                logger.debug("Tags already up to date for %s", meta.path)
            if target_path:
                self.organizer.move(meta, target_path, dry_run=False)
                self.cache.record_move(original_path, target_path)
        except ProcessingError as exc:
            logger.warning("Failed to update tags for %s: %s", meta.path, exc)
            return
        stat_after = self._safe_stat(meta.path)
        if stat_after:
            self.cache.set_processed_file(
                meta.path,
                stat_after.st_mtime_ns,
                stat_after.st_size,
                organized_flag,
            )

    def _needs_supplement(self, meta: TrackMetadata) -> bool:
        return not meta.album or not meta.artist or not meta.album_artist

    @staticmethod
    def _safe_stat(path: Path):
        try:
            return path.stat()
        except FileNotFoundError:
            return None

    @staticmethod
    def _warn_ambiguous_release(directory: Path, releases: list[tuple[str, float, str]]) -> None:
        entries = ", ".join(
            f"{title or release_id} ({release_id}, score={score:.2f})" for release_id, score, title in releases
        )
        logger.warning(
            "Ambiguous release detection for %s – multiple albums scored similarly: %s. "
            "Skipping this directory; adjust tags or split folders, then rerun.",
            directory,
            entries,
        )
