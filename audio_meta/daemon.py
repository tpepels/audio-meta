from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .classical import ClassicalHeuristics
from .heuristics import guess_metadata_from_path
from .config import Settings
from .models import ProcessingError, TrackMetadata
from .organizer import Organizer
from .providers.discogs import DiscogsClient
from .providers.musicbrainz import LookupResult, MusicBrainzClient
from .scanner import DirectoryBatch, LibraryScanner
from .tagging import TagWriter
from .cache import MetadataCache

logger = logging.getLogger(__name__)

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_MAGENTA = "\033[35m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"


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


@dataclass
class ReleaseExample:
    provider: str
    title: str
    artist: str
    date: Optional[str]
    track_total: Optional[int]
    disc_count: Optional[int]
    formats: list[str]


@dataclass
class PendingResult:
    meta: TrackMetadata
    result: Optional[LookupResult]
    matched: bool


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
    def __init__(
        self,
        settings: Settings,
        dry_run_output: Optional[Path] = None,
        interactive: bool = False,
        release_cache_enabled: bool = True,
        defer_prompts: bool = False,
    ) -> None:
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
        self.interactive = interactive
        self.release_cache_enabled = release_cache_enabled
        self.defer_prompts = bool(defer_prompts and interactive)
        self._use_color = sys.stdout.isatty()
        self.skip_reasons: dict[Path, str] = {}
        self._skip_lock = Lock()
        self._processed_albums: set[Path] = set()
        self._library_roots = [root.resolve() for root in settings.library.roots]
        if self.dry_run_recorder:
            logger.debug("Dry-run mode enabled; writing preview to %s", dry_run_output)
        self._release_sep = ":"
        self._defer_lock = Lock()
        self._deferred_directories: list[Path] = []
        self._deferred_set: set[Path] = set()
        self._processing_deferred = False
        self.archive_root = settings.organizer.archive_root

    async def run_scan(self) -> None:
        logger.debug("Starting one-off scan")
        for batch in self.scanner.iter_directories():
            await self.queue.put(batch)
        workers = self._start_workers()
        await self.queue.join()
        await self._stop_workers(workers)
        if self.defer_prompts:
            self._process_deferred_directories()

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
        concurrency = 1 if self.interactive else self.settings.daemon.worker_concurrency
        return [asyncio.create_task(self._worker(i)) for i in range(concurrency)]

    async def _stop_workers(self, workers: list[asyncio.Task[None]]) -> None:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self, worker_id: int) -> None:
        while True:
            batch = await self.queue.get()
            try:
                if self.cache.is_directory_ignored(batch.directory):
                    logger.info("Skipping ignored directory %s", self._display_path(batch.directory))
                else:
                    await asyncio.get_event_loop().run_in_executor(None, self._process_directory, batch)
            except Exception:  # pragma: no cover - logged and ignored
                logger.exception("Worker %s failed to process %s", worker_id, batch.directory)
            finally:
                self.queue.task_done()

    def _process_directory(self, batch: DirectoryBatch, force_prompt: bool = False) -> None:
        prepared = self._prepare_album_batch(batch)
        if not prepared:
            return
        batch = prepared
        if self._directory_already_processed(batch):
            logger.debug("Skipping %s; already processed and organized", batch.directory)
            return
        planned: list[PlannedUpdate] = []
        release_summary_printed = False
        logger.debug("Processing directory %s with %d files", batch.directory, len(batch.files))
        pending_results: list[PendingResult] = []
        release_scores: dict[str, float] = {}
        release_examples: dict[str, ReleaseExample] = {}
        discogs_details: dict[str, dict] = {}
        dir_track_count, dir_year = self._directory_context(batch.directory, batch.files)
        cached_discogs_release_details = None
        forced_provider: Optional[str] = None
        forced_release_id: Optional[str] = None
        forced_release_score: float = 0.0
        cached_release_entry = self._cached_release_for_directory(batch.directory)
        if cached_release_entry:
            provider, cached_release_id, cached_score = cached_release_entry
            forced_provider = provider
            forced_release_id = cached_release_id
            forced_release_score = cached_score
            if provider == "musicbrainz":
                self.musicbrainz.release_tracker.register(
                    batch.directory,
                    cached_release_id,
                    self.musicbrainz._fetch_release_tracks,
                )
                self.musicbrainz.release_tracker.remember_release(batch.directory, cached_release_id, cached_score)
                release_data = self.musicbrainz.release_tracker.releases.get(cached_release_id)
                if release_data:
                    key = self._release_key("musicbrainz", cached_release_id)
                    release_examples[key] = ReleaseExample(
                        provider="musicbrainz",
                        title=release_data.album_title or "",
                        artist=release_data.album_artist or "",
                        date=release_data.release_date,
                        track_total=len(release_data.tracks) if release_data.tracks else None,
                        disc_count=release_data.disc_count or None,
                        formats=list(release_data.formats),
                    )
                key = self._release_key("musicbrainz", cached_release_id)
                release_scores[key] = max(release_scores.get(key, 0.0), cached_score or 1.0)
            elif provider == "discogs" and self.discogs:
                cached_discogs_release_details = self.discogs.get_release(int(cached_release_id))
                if cached_discogs_release_details:
                    key = self._release_key("discogs", cached_release_id)
                    discogs_details[key] = cached_discogs_release_details
                    release_examples[key] = ReleaseExample(
                        provider="discogs",
                        title=cached_discogs_release_details.get("title") or "",
                        artist=self._discogs_release_artist(cached_discogs_release_details) or "",
                        date=str(cached_discogs_release_details.get("year") or ""),
                        track_total=len(cached_discogs_release_details.get("tracklist") or []),
                        disc_count=cached_discogs_release_details.get("disc_count"),
                        formats=cached_discogs_release_details.get("formats") or [],
                    )
                    release_scores[key] = max(release_scores.get(key, 0.0), cached_score or 1.0)

        for file_path in batch.files:
            meta = TrackMetadata(path=file_path)
            if dir_track_count:
                meta.extra["TRACK_TOTAL"] = str(dir_track_count)
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
            pending_results.append(PendingResult(meta=meta, result=result, matched=bool(result)))
            if result and meta.musicbrainz_release_id:
                release_id = meta.musicbrainz_release_id
                key = self._release_key("musicbrainz", release_id)
                release_scores[key] = max(release_scores.get(key, 0.0), result.score)
                release_data = self.musicbrainz.release_tracker.releases.get(release_id)
                release_examples[self._release_key("musicbrainz", release_id)] = ReleaseExample(
                    provider="musicbrainz",
                    title=release_data.album_title if release_data and release_data.album_title else meta.album or "",
                    artist=release_data.album_artist if release_data and release_data.album_artist else meta.album_artist or meta.artist or "",
                    date=release_data.release_date if release_data else None,
                    track_total=len(release_data.tracks) if release_data and release_data.tracks else None,
                    disc_count=release_data.disc_count if release_data and release_data.disc_count else None,
                    formats=list(release_data.formats) if release_data else [],
                )
        discogs_release_details = None
        if not release_scores and cached_discogs_release_details:
            self._apply_discogs_release_details(pending_results, cached_discogs_release_details)
        if not release_scores and self.interactive and pending_results:
            sample_meta = pending_results[0].meta if pending_results else None
            if sample_meta and dir_track_count:
                sample_meta.extra.setdefault("TRACK_TOTAL", str(dir_track_count))
            selection = self._resolve_unmatched_directory(
                batch.directory,
                sample_meta,
                dir_track_count,
                dir_year,
            )
            if selection is None:
                logger.warning("Skipping %s; no manual release selected", batch.directory)
                return
            provider, selection_id = selection
            if provider == "discogs":
                if not self.discogs:
                    self._record_skip(batch.directory, "Discogs provider unavailable for manual selection")
                    logger.warning("Discogs provider unavailable; cannot apply manual selection for %s", batch.directory)
                    return
                discogs_release_details = self.discogs.get_release(int(selection_id))
                if not discogs_release_details:
                    self._record_skip(batch.directory, f"Failed to load Discogs release {selection_id}")
                    logger.warning("Failed to load Discogs release %s; skipping %s", selection_id, batch.directory)
                    return
                self._apply_discogs_release_details(pending_results, discogs_release_details)
                discogs_artist = self._discogs_release_artist(discogs_release_details)
                disc_track_count, disc_disc_total = self._discogs_counts(discogs_release_details)
                self._persist_directory_release(
                    batch.directory,
                    "discogs",
                    selection_id,
                    1.0,
                    artist_hint=discogs_artist,
                    album_hint=discogs_release_details.get("title"),
                )
                self._print_release_selection_summary(
                    batch.directory,
                    "discogs",
                    selection_id,
                    discogs_release_details.get("title"),
                    discogs_artist,
                    disc_track_count,
                    disc_disc_total,
                    pending_results,
                )
                release_summary_printed = True
                key = self._release_key("discogs", selection_id)
                discogs_details[key] = discogs_release_details
                best_release_id = key
                best_score = 1.0
                release_scores[key] = 1.0
            else:
                applied = self._apply_musicbrainz_release_selection(
                    batch.directory,
                    selection_id,
                    pending_results,
                    force=True,
                )
                if not applied:
                    self._record_skip(batch.directory, f"Manual MusicBrainz release {selection_id} did not match tracks")
                    logger.warning("Manual MusicBrainz release %s did not match tracks in %s", selection_id, batch.directory)
                    return
                release_data = self.musicbrainz.release_tracker.releases.get(selection_id)
                if release_data:
                    key = self._release_key("musicbrainz", selection_id)
                    release_examples[key] = ReleaseExample(
                        provider="musicbrainz",
                        title=release_data.album_title or "",
                        artist=release_data.album_artist or "",
                        date=release_data.release_date,
                        track_total=len(release_data.tracks) if release_data.tracks else None,
                        disc_count=release_data.disc_count or None,
                        formats=list(release_data.formats),
                    )
                    self._print_release_selection_summary(
                        batch.directory,
                        "musicbrainz",
                        selection_id,
                        release_data.album_title,
                        release_data.album_artist,
                        len(release_data.tracks) if release_data.tracks else None,
                        release_data.disc_count,
                        pending_results,
                    )
                    release_summary_printed = True
                key = self._release_key("musicbrainz", selection_id)
                release_scores[key] = max(release_scores.get(key, 0.0), 1.0)
                best_release_id = key
                best_score = 1.0

        if self.discogs and pending_results:
            sample_meta = pending_results[0].meta if pending_results else None
            if sample_meta:
                if dir_track_count:
                    sample_meta.extra.setdefault("TRACK_TOTAL", str(dir_track_count))
                for cand in self._discogs_candidates(sample_meta):
                    release_id = str(cand.get("id"))
                    if not release_id:
                        continue
                    key = self._release_key("discogs", release_id)
                    if key in release_scores:
                        continue
                    base_score = cand.get("score")
                    base_score = base_score if isinstance(base_score, (int, float)) else 0.5
                    release_scores[key] = base_score
                    release_examples[key] = ReleaseExample(
                        provider="discogs",
                        title=cand.get("title") or "",
                        artist=cand.get("artist") or "",
                        date=str(cand.get("year") or ""),
                        track_total=cand.get("track_count"),
                        disc_count=cand.get("disc_count"),
                        formats=list(cand.get("formats") or []),
                    )
                    details = cand.get("details")
                    if details:
                        discogs_details[key] = details

        release_scores, coverage_map = self._adjust_release_scores(
            release_scores,
            release_examples,
            dir_track_count,
            dir_year,
            pending_results,
            batch.directory,
            discogs_details,
        )
        best_release_id = None
        best_score = 0.0
        for rid, score in release_scores.items():
            if score > best_score:
                best_release_id = rid
                best_score = score
        ambiguous_cutoff = 0.05
        if forced_provider and forced_release_id:
            key = self._release_key(forced_provider, forced_release_id)
            best_release_id = key
            best_score = release_scores.get(key, forced_release_score or 1.0)
            release_scores[key] = best_score
        ambiguous_candidates = [
            (rid, score) for rid, score in release_scores.items() if best_release_id and best_score - score <= ambiguous_cutoff
        ]
        if forced_provider and forced_release_id and best_release_id == self._release_key(forced_provider, forced_release_id):
            forced_key = self._release_key(forced_provider, forced_release_id)
            ambiguous_candidates = [(forced_key, best_score)]
        if len(ambiguous_candidates) > 1:
            auto_pick = self._auto_pick_equivalent_release(
                ambiguous_candidates,
                release_examples,
                discogs_details,
            )
            if auto_pick:
                best_release_id = auto_pick
                best_score = release_scores.get(auto_pick, best_score)
                ambiguous_candidates = [(auto_pick, best_score)]
        coverage_threshold = 0.7
        coverage = coverage_map.get(best_release_id, 1.0) if best_release_id else 1.0
        if best_release_id and coverage < coverage_threshold:
            if self.defer_prompts and not force_prompt and not self._processing_deferred:
                self._schedule_deferred_directory(batch.directory, "low_coverage")
                return
            if self.interactive:
                logger.warning(
                    "Release %s matches only %.0f%% of tracks in %s; confirmation required",
                    best_release_id,
                    coverage * 100,
                    self._display_path(batch.directory),
                )
                top_candidates = sorted(release_scores.items(), key=lambda x: x[1], reverse=True)[:5]
                sample_meta = pending_results[0].meta if pending_results else None
                if sample_meta and dir_track_count:
                    sample_meta.extra.setdefault("TRACK_TOTAL", str(dir_track_count))
                selection = self._resolve_release_interactively(
                    batch.directory,
                    top_candidates,
                    release_examples,
                    sample_meta,
                    dir_track_count,
                    dir_year,
                    discogs_details,
                )
                if selection is None:
                    self._record_skip(batch.directory, "User skipped low-coverage release selection")
                    logger.warning("Skipping %s due to low coverage", batch.directory)
                    return
                provider, selection_id = selection
                best_release_id = self._release_key(provider, selection_id)
                best_score = release_scores.get(best_release_id, 1.0)
                ambiguous_candidates = [(best_release_id, best_score)]
                coverage = coverage_map.get(best_release_id, 1.0)
                forced_provider = provider
                forced_release_id = selection_id
            else:
                logger.warning(
                    "Release %s matches only %.0f%% of tracks in %s; skipping in non-interactive mode",
                    best_release_id,
                    coverage * 100,
                    self._display_path(batch.directory),
                )
                self._record_skip(batch.directory, "Low coverage release match")
                return
        if best_release_id and len(ambiguous_candidates) > 1:
            if self.defer_prompts and not force_prompt and not self._processing_deferred:
                self._schedule_deferred_directory(batch.directory, "ambiguous_release")
                return
            if self.interactive:
                sample_meta = pending_results[0].meta if pending_results else None
                if sample_meta and dir_track_count:
                    sample_meta.extra.setdefault("TRACK_TOTAL", str(dir_track_count))
                selection = self._resolve_release_interactively(
                    batch.directory,
                    ambiguous_candidates,
                    release_examples,
                    sample_meta,
                    dir_track_count,
                    dir_year,
                    discogs_details,
                )
                if selection is None:
                    self._record_skip(batch.directory, "User skipped ambiguous release selection")
                    logger.warning("Skipping %s per user choice", batch.directory)
                    return
                provider, selection_id = selection
                if provider == "discogs":
                    if not self.discogs:
                        self._record_skip(batch.directory, "Discogs provider unavailable for manual selection")
                        logger.warning("Discogs provider unavailable; cannot use selection for %s", batch.directory)
                        return
                    discogs_release_details = self.discogs.get_release(int(selection_id))
                    if not discogs_release_details:
                        self._record_skip(batch.directory, f"Failed to load Discogs release {selection_id}")
                        logger.warning("Failed to load Discogs release %s; skipping %s", selection_id, batch.directory)
                        return
                    discogs_artist = self._discogs_release_artist(discogs_release_details)
                    self._persist_directory_release(
                        batch.directory,
                        "discogs",
                        selection_id,
                        1.0,
                        artist_hint=discogs_artist,
                        album_hint=discogs_release_details.get("title"),
                    )
                    self._print_release_selection_summary(
                        batch.directory,
                        "discogs",
                        selection_id,
                        discogs_release_details.get("title"),
                        discogs_artist,
                        discogs_release_details.get("track_count"),
                        discogs_release_details.get("disc_count"),
                        pending_results,
                    )
                    release_summary_printed = True
                    key = self._release_key("discogs", selection_id)
                    discogs_details[key] = discogs_release_details
                    best_release_id = key
                    best_score = 1.0
                    release_scores[key] = 1.0
                else:
                    key = self._release_key("musicbrainz", selection_id)
                    best_release_id = key
                    best_score = next(score for rid, score in ambiguous_candidates if rid == key)
                    self._apply_musicbrainz_release_selection(
                        batch.directory,
                        selection_id,
                        pending_results,
                        force=True,
                    )
                    release_data = self.musicbrainz.release_tracker.releases.get(selection_id)
                    if release_data:
                        self._print_release_selection_summary(
                            batch.directory,
                            "musicbrainz",
                            selection_id,
                            release_data.album_title,
                            release_data.album_artist,
                            len(release_data.tracks) if release_data.tracks else None,
                            release_data.disc_count,
                            pending_results,
                        )
                        release_summary_printed = True
                    release_scores[key] = max(release_scores.get(key, 0.0), best_score)
            else:
                self._warn_ambiguous_release(
                    batch.directory,
                    [
                        (
                            rid,
                            score,
                            release_examples.get(rid),
                        )
                        for rid, score in ambiguous_candidates
                    ],
                    dir_track_count,
                    dir_year,
                )
                self._record_skip(batch.directory, "Ambiguous release matches in non-interactive mode")
                return
        applied_provider: Optional[str] = None
        applied_release_plain_id: Optional[str] = None
        if best_release_id:
            best_provider, best_release_plain_id = self._split_release_key(best_release_id)
            applied_provider = best_provider
            applied_release_plain_id = best_release_plain_id
            example = release_examples.get(best_release_id)
            album_name = example.title if example and example.title else ""
            album_artist = example.artist if example and example.artist else ""
            if best_provider == "musicbrainz":
                release_ref = self.musicbrainz.release_tracker.releases.get(best_release_plain_id)
                if not release_ref:
                    release_ref = self.musicbrainz._fetch_release_tracks(best_release_plain_id)
                    if release_ref:
                        self.musicbrainz.release_tracker.releases[best_release_plain_id] = release_ref
                if not release_ref:
                    self._record_skip(batch.directory, f"MusicBrainz release {best_release_plain_id} unavailable")
                    logger.warning("MusicBrainz release %s unavailable for %s", best_release_plain_id, batch.directory)
                    return
                if not album_name:
                    album_name = release_ref.album_title or ""
                if not album_artist:
                    album_artist = release_ref.album_artist or ""
                if not release_summary_printed:
                    self._print_release_selection_summary(
                        batch.directory,
                        "musicbrainz",
                        best_release_plain_id,
                        album_name,
                        album_artist,
                        len(release_ref.tracks) if release_ref.tracks else None,
                        release_ref.disc_count,
                        pending_results,
                    )
                    release_summary_printed = True
                self._persist_directory_release(
                    batch.directory,
                    "musicbrainz",
                    best_release_plain_id,
                    best_score,
                    artist_hint=album_artist,
                    album_hint=album_name,
                )
            else:
                details = discogs_details.get(best_release_id)
                if not details and self.discogs:
                    try:
                        details = self.discogs.get_release(int(best_release_plain_id))
                    except Exception as exc:  # pragma: no cover
                        logger.warning("Failed to load Discogs release %s: %s", best_release_plain_id, exc)
                        details = None
                    if details:
                        discogs_details[best_release_id] = details
                if not details:
                    self._record_skip(batch.directory, f"Discogs release {best_release_plain_id} unavailable")
                    logger.warning("Discogs release %s unavailable for %s", best_release_plain_id, batch.directory)
                    return
                discogs_release_details = details
                self._apply_discogs_release_details(pending_results, details)
                album_name = details.get("title") or album_name
                discogs_artist = self._discogs_release_artist(details)
                album_artist = discogs_artist or album_artist
                if not release_summary_printed:
                    self._print_release_selection_summary(
                        batch.directory,
                        "discogs",
                        best_release_plain_id,
                        album_name,
                        album_artist,
                        example.track_total if example else None,
                        example.disc_count if example else None,
                        pending_results,
                    )
                    release_summary_printed = True
                self._persist_directory_release(
                    batch.directory,
                    "discogs",
                    best_release_plain_id,
                    best_score,
                    artist_hint=album_artist,
                    album_hint=album_name,
                )
        else:
            album_name = album_artist = ""

        for pending in pending_results:
            meta = pending.meta
            result = pending.result
            matched = pending.matched
            if discogs_release_details:
                self.discogs.apply_release_details(meta, discogs_release_details, allow_overwrite=True)
                matched = True
                pending.matched = True
            if not matched:
                logger.warning("No metadata match for %s; leaving file untouched", meta.path)
                continue
            if best_release_id:
                if applied_provider == "musicbrainz":
                    if album_name:
                        meta.album = album_name
                    if album_artist:
                        meta.album_artist = album_artist
                    if applied_release_plain_id:
                        meta.musicbrainz_release_id = applied_release_plain_id
                else:
                    if album_name:
                        meta.album = album_name
                    if album_artist:
                        meta.album_artist = album_artist
                    meta.musicbrainz_release_id = None
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
        unmatched_entries = [p for p in pending_results if not p.matched]
        if unmatched_entries and best_release_id:
            if self.defer_prompts and not force_prompt and not self._processing_deferred:
                self._schedule_deferred_directory(batch.directory, "unmatched_tracks")
                return
            if self.interactive:
                if not self._prompt_on_unmatched_release(batch.directory, best_release_id, unmatched_entries):
                    self._record_skip(batch.directory, "User skipped due to unmatched tracks")
                    logger.warning(
                        "Skipping %s because %d tracks did not match release %s",
                        batch.directory,
                        len(unmatched_entries),
                        best_release_id,
                    )
                    return
            else:
                logger.warning(
                    "Release %s left %d tracks unmatched in %s",
                    best_release_id,
                    len(unmatched_entries),
                    self._display_path(batch.directory),
                )

        if not planned:
            if not any(p.matched for p in pending_results):
                self._record_skip(batch.directory, "No metadata match found for directory")
            logger.debug("No actionable files in %s", batch.directory)
            return
        for plan in planned:
            self._apply_plan(plan)

    def _schedule_deferred_directory(self, directory: Path, reason: str) -> None:
        if not self.defer_prompts:
            return
        with self._defer_lock:
            if directory in self._deferred_set:
                return
            self._deferred_set.add(directory)
            self._deferred_directories.append(directory)
        logger.info("Deferring %s (%s); will request input later", self._display_path(directory), reason)

    def _process_deferred_directories(self) -> None:
        with self._defer_lock:
            pending = list(self._deferred_directories)
            self._deferred_directories.clear()
            self._deferred_set.clear()
        if not pending:
            return
        suffix = "ies" if len(pending) != 1 else "y"
        logger.info("Processing %d deferred directory%s...", len(pending), suffix)
        self._processing_deferred = True
        try:
            for directory in pending:
                batch = self.scanner.collect_directory(directory)
                if not batch:
                    logger.warning("Deferred directory %s no longer exists; skipping", self._display_path(directory))
                    continue
                self._process_directory(batch, force_prompt=True)
        finally:
            self._processing_deferred = False

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
                self.organizer.cleanup_source_directory(original_path.parent)
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

    def _record_skip(self, directory: Path, reason: str) -> None:
        with self._skip_lock:
            self.skip_reasons[directory] = reason

    def _adjust_release_scores(
        self,
        scores: dict[str, float],
        release_examples: dict[str, ReleaseExample],
        dir_track_count: int,
        dir_year: Optional[int],
        pending_results: list[PendingResult],
        directory: Path,
        discogs_details: dict[str, dict],
    ) -> tuple[dict[str, float], dict[str, float]]:
        adjusted: dict[str, float] = {}
        coverage_map: dict[str, float] = {}
        for key, base_score in scores.items():
            example = release_examples.get(key)
            bonus = 0.0
            release_track_total = example.track_total if example else None
            if dir_track_count and release_track_total:
                ratio = min(dir_track_count, release_track_total) / max(dir_track_count, release_track_total)
                if ratio >= 0.95:
                    bonus += 0.08
                elif ratio >= 0.85:
                    bonus += 0.05
                elif ratio >= 0.7:
                    bonus += 0.02
                elif ratio <= 0.55:
                    bonus -= 0.07
                elif ratio <= 0.4:
                    bonus -= 0.12
            release_year = self._parse_year(example.date if example else None)
            if dir_year and release_year:
                diff = abs(release_year - dir_year)
                if diff == 0:
                    bonus += 0.035
                elif diff == 1:
                    bonus += 0.015
                elif diff >= 3:
                    bonus -= 0.03
            bonus += self._tag_overlap_bonus(example, pending_results, directory)
            coverage = 1.0
            extra_bonus, coverage = self._release_match_quality(
                key,
                pending_results,
                discogs_details,
            )
            bonus += extra_bonus
            coverage_map[key] = coverage
            adjusted[key] = base_score + bonus
        return adjusted, coverage_map

    def _tag_overlap_bonus(
        self,
        example: Optional[ReleaseExample],
        pending_results: list[PendingResult],
        directory: Path,
    ) -> float:
        if not example:
            return 0.0
        bonus = 0.0
        first_meta = pending_results[0].meta if pending_results else None
        meta_artist = None
        meta_album = None
        if first_meta:
            meta_artist = first_meta.album_artist or first_meta.artist
            meta_album = first_meta.album
        release_artist = example.artist or None
        release_album = example.title or None
        bonus += self._overlap_delta(self._token_overlap_ratio(meta_artist, release_artist))
        bonus += self._overlap_delta(self._token_overlap_ratio(meta_album, release_album))
        hint_artist, hint_album = self._path_based_hints(directory)
        bonus += 0.5 * self._overlap_delta(self._token_overlap_ratio(hint_artist, release_artist))
        bonus += 0.5 * self._overlap_delta(self._token_overlap_ratio(hint_album, release_album))
        return max(-0.05, min(0.05, bonus))

    @staticmethod
    def _overlap_delta(ratio: Optional[float]) -> float:
        if ratio is None:
            return 0.0
        if ratio >= 0.75:
            return 0.02
        if ratio >= 0.6:
            return 0.01
        if ratio <= 0.2:
            return -0.02
        return 0.0

    def _release_match_quality(
        self,
        key: str,
        pending_results: list[PendingResult],
        discogs_details: dict[str, dict],
    ) -> tuple[float, float]:
        provider, release_id = self._split_release_key(key)
        if provider != "musicbrainz":
            return 0.0, 1.0
        release_data = self.musicbrainz.release_tracker.releases.get(release_id)
        if not release_data:
            release_data = self.musicbrainz._fetch_release_tracks(release_id)
            if release_data:
                self.musicbrainz.release_tracker.releases[release_id] = release_data
        if not release_data or not release_data.tracks:
            return 0.0, 0.0
        total = 0.0
        count = 0
        for pending in pending_results:
            track_score = self._match_pending_to_release(pending.meta, release_data)
            if track_score is not None:
                total += track_score
                count += 1
        coverage = count / len(pending_results) if pending_results else 0.0
        if not count:
            return 0.0, coverage
        avg = total / count
        return min(0.08, avg * 0.08), coverage

    def _auto_pick_equivalent_release(
        self,
        candidates: list[tuple[str, float]],
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[str]:
        signatures: dict[str, tuple[int, tuple[tuple[str, Optional[int]], ...]]] = {}
        for key, _ in candidates:
            signature = self._canonical_release_signature(key, release_examples, discogs_details)
            if signature is None:
                return None
            signatures[key] = signature
        iterator = iter(signatures.values())
        try:
            first_signature = next(iterator)
        except StopIteration:
            return None
        if not all(sig == first_signature for sig in iterator):
            return None
        priority = {"musicbrainz": 0, "discogs": 1}
        best_key = min(
            signatures.keys(),
            key=lambda key: (priority.get(self._split_release_key(key)[0], 99), key),
        )
        return best_key

    def _canonical_release_signature(
        self,
        key: str,
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[tuple[int, tuple[tuple[str, Optional[int]], ...]]]:
        entries = self._release_track_entries(key, release_examples, discogs_details)
        if not entries:
            return None
        normalized = []
        for title, duration in entries:
            if not title:
                return None
            normalized.append((title, duration))
        return len(normalized), tuple(normalized)

    def _release_track_entries(
        self,
        key: str,
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
    ) -> Optional[list[tuple[str, Optional[int]]]]:
        provider, release_id = self._split_release_key(key)
        if provider == "musicbrainz":
            release_data = self.musicbrainz.release_tracker.releases.get(release_id)
            if not release_data:
                release_data = self.musicbrainz._fetch_release_tracks(release_id)
                if release_data:
                    self.musicbrainz.release_tracker.releases[release_id] = release_data
            if not release_data or not release_data.tracks:
                return None
            entries: list[tuple[str, Optional[int]]] = []
            for track in release_data.tracks:
                if not track.title:
                    return None
                entries.append((self._normalize_match_text(track.title), track.duration_seconds))
            return entries
        if provider == "discogs":
            details = discogs_details.get(key)
            if not details and self.discogs:
                try:
                    details = self.discogs.get_release(int(release_id))
                except (ValueError, TypeError):
                    details = None
                if details:
                    discogs_details[key] = details
            if not details:
                return None
            tracklist = details.get("tracklist") or []
            entries = []
            for track in tracklist:
                if track.get("type_", "track") not in (None, "", "track"):
                    continue
                title = track.get("title")
                if not title:
                    return None
                entries.append((self._normalize_match_text(title), self._parse_discogs_duration(track.get("duration"))))
            return entries or None
        return None

    @staticmethod
    def _parse_discogs_duration(value: Optional[str]) -> Optional[int]:
        if not value or ":" not in value:
            return None
        parts = value.split(":", 1)
        try:
            minutes = int(parts[0])
            seconds_str = parts[1]
            if seconds_str.isdigit():
                seconds = int(seconds_str)
            else:
                seconds = int(float(seconds_str))
            return minutes * 60 + seconds
        except (ValueError, TypeError):
            return None

    def _match_pending_to_release(self, meta: TrackMetadata, release: "ReleaseData") -> Optional[float]:
        title = meta.title or guess_metadata_from_path(meta.path).title
        duration = meta.duration_seconds
        if duration is None:
            duration = self.musicbrainz._probe_duration(meta.path)
            if duration:
                meta.duration_seconds = duration
        best = 0.0
        for track in release.tracks:
            combined = self._combine_similarity(
                self._title_similarity(title, track.title),
                self._duration_similarity(duration, track.duration_seconds),
            )
            if combined is not None and combined > best:
                best = combined
        if best <= 0.0:
            return None
        return best

    def _title_similarity(self, a: Optional[str], b: Optional[str]) -> Optional[float]:
        if not a or not b:
            return None
        norm_a = self._normalize_match_text(a)
        norm_b = self._normalize_match_text(b)
        if not norm_a or not norm_b:
            return None
        return SequenceMatcher(None, norm_a, norm_b).ratio()

    @staticmethod
    def _duration_similarity(a: Optional[int], b: Optional[int]) -> Optional[float]:
        if not a or not b:
            return None
        diff = abs(a - b)
        if diff > max(20, int(0.25 * max(a, b))):
            return max(0.0, 1 - diff / (max(a, b) or 1))
        return max(0.0, 1 - diff / (max(a, b) or 1))

    @staticmethod
    def _combine_similarity(title_ratio: Optional[float], duration_ratio: Optional[float]) -> Optional[float]:
        score = 0.0
        weight = 0.0
        if title_ratio is not None:
            score += title_ratio * 0.7
            weight += 0.7
        if duration_ratio is not None:
            score += duration_ratio * 0.3
            weight += 0.3
        if weight == 0.0:
            return None
        return score / weight

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        cleaned = unicodedata.normalize("NFKD", value)
        cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
        cleaned = cleaned.lower()
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
        return cleaned.strip()

    @staticmethod
    def _safe_stat(path: Path):
        try:
            return path.stat()
        except FileNotFoundError:
            return None

    @staticmethod
    def _warn_ambiguous_release(
        directory: Path,
        releases: list[tuple[str, float, Optional[ReleaseExample]]],
        dir_track_count: int,
        dir_year: Optional[int],
    ) -> None:
        hint = f"{dir_track_count} audio files" if dir_track_count else "unknown track count"
        if dir_year:
            hint = f"{hint}; year hint {dir_year}"
        entry_texts = []
        for key, score, example in releases:
            provider, release_id = AudioMetaDaemon._split_release_key(key)
            entry_texts.append(
                f"[{provider[:2].upper()}] {(example.title if example else '') or release_id} "
                f"({release_id}, score={score:.2f}, year={AudioMetaDaemon._parse_year(example.date if example else None) or '?'}, "
                f"tracks={example.track_total if example and example.track_total else '?'})"
            )
        entries = ", ".join(entry_texts)
        logger.warning(
            "Ambiguous release detection for %s (%s) – multiple albums scored similarly: %s. "
            "Skipping this directory; adjust tags or split folders, then rerun.",
            self._display_path(directory),
            hint,
            entries,
        )

    def _resolve_release_interactively(
        self,
        directory: Path,
        candidates: list[tuple[str, float]],
        release_examples: dict[str, ReleaseExample],
        sample_meta: Optional[TrackMetadata],
        dir_track_count: int,
        dir_year: Optional[int],
        discogs_details: dict[str, dict],
    ) -> Optional[tuple[str, str]]:
        options: list[dict] = []
        idx = 1
        for key, score in sorted(candidates, key=lambda x: x[1], reverse=True):
            provider, release_id = self._split_release_key(key)
            example = release_examples.get(key)
            title = example.title if example else ""
            artist = example.artist if example else ""
            year = self._parse_year(example.date if example else None) or "?"
            track_count = example.track_total if example else None
            disc_count = example.disc_count if example else None
            formats = example.formats if example else []
            disc_label = self._disc_label(disc_count) or "disc count unknown"
            format_label = ", ".join(formats) if formats else "format unknown"
            tag = "MB" if provider == "musicbrainz" else "DG"
            label = self._format_option_label(
                idx,
                tag,
                artist or "Unknown Artist",
                title or "Unknown Title",
                year,
                track_count or "?",
                disc_label,
                format_label,
                score,
                release_id,
            )
            options.append({"idx": idx, "provider": provider, "id": release_id, "label": label, "score": score})
            idx += 1
        if sample_meta and self.discogs:
            seen_pairs = {(opt["provider"], opt["id"]) for opt in options}
            for cand in self._discogs_candidates(sample_meta):
                release_id = str(cand.get("id") or "").strip()
                if not release_id:
                    continue
                pair = ("discogs", release_id)
                if pair in seen_pairs:
                    continue
                label = self._format_option_label(
                    idx,
                    "DG",
                    cand.get("artist") or "Unknown",
                    cand.get("title") or "Unknown Title",
                    cand.get("year") or "?",
                    cand.get("track_count") or "?",
                    cand.get("disc_label") or "disc count unknown",
                    cand.get("format_label") or "format unknown",
                    None,
                    cand["id"],
                )
                options.append(
                    {
                        "idx": idx,
                        "provider": "discogs",
                        "id": release_id,
                        "label": label,
                        "score": cand.get("score", 0.0),
                    }
                )
                seen_pairs.add(pair)
                idx += 1
        if not options:
            self._record_skip(directory, "No interactive release options available")
            logger.warning("No interactive options available for %s", directory)
            return None
        options.sort(key=lambda opt: (opt.get("score") or 0.0), reverse=True)
        year_hint = f"{dir_year}" if dir_year else "unknown"
        display = self._display_path(directory)
        print(f"\nAmbiguous release for {display} – {dir_track_count} tracks detected, year hint {year_hint}:")
        for option in options:
            print(f"  {option['idx']}. {option['label']}")
        print("  0. Skip this directory")
        print("  d. Delete this directory")
        print("  a. Archive this directory")
        print("  i. Ignore this directory")
        print("  mb:<release-id> or dg:<release-id> to enter an ID manually")
        while True:
            choice = input("Select release (0=skip): ").strip()
            if choice.lower() in {"0", "s", "skip"}:
                return None
            if choice.lower() in {"d", "del", "delete"}:
                if self._delete_directory(directory):
                    self._record_skip(directory, "Directory deleted per user request")
                return None
            if choice.lower() in {"a", "archive"}:
                if not self._archive_directory(directory):
                    continue
                self.cache.ignore_directory(directory, "archived")
                return None
            if choice.lower() in {"i", "ignore"}:
                self.cache.ignore_directory(directory, "user request")
                logger.info("Ignoring %s per user request", self._display_path(directory))
                return None
            manual = self._parse_manual_release_choice(choice)
            if manual:
                return manual
            if not choice.isdigit():
                print("Invalid selection; enter a number or mb:/dg: identifier.")
                continue
            number = int(choice)
            match = next((opt for opt in options if opt["idx"] == number), None)
            if not match:
                print("Selection out of range.")
                continue
            return match["provider"], match["id"]

    def _resolve_unmatched_directory(
        self,
        directory: Path,
        sample_meta: Optional[TrackMetadata],
        dir_track_count: int,
        dir_year: Optional[int],
    ) -> Optional[tuple[str, str]]:
        if not sample_meta:
            self._record_skip(directory, "No sample metadata for manual selection")
            return None
        artist_hint, album_hint = self._directory_hints(sample_meta, directory)
        options: list[dict] = []
        idx = 1
        mb_candidates = self.musicbrainz.search_release_candidates(artist_hint, album_hint, limit=6)
        for cand in mb_candidates:
            year = self._parse_year(cand.get("date")) or "?"
            track_count = cand.get("track_total") or "?"
            disc_label = self._disc_label(cand.get("disc_count")) or "disc count unknown"
            format_label = ", ".join(cand.get("formats") or []) or "format unknown"
            score = cand.get("score")
            label = self._format_option_label(
                idx,
                "MB",
                cand.get("artist") or "Unknown Artist",
                cand.get("title") or "Unknown Title",
                year,
                track_count,
                disc_label,
                format_label,
                score,
                cand["id"],
            )
            options.append({"idx": idx, "provider": "musicbrainz", "id": cand["id"], "label": label, "score": score})
            idx += 1
        if self.discogs and sample_meta:
            for cand in self._discogs_candidates(sample_meta):
                label = self._format_option_label(
                    idx,
                    "DG",
                    cand.get("artist") or "Unknown",
                    cand.get("title") or "Unknown Title",
                    cand.get("year") or "?",
                    cand.get("track_count") or "?",
                    cand.get("disc_label") or "disc count unknown",
                    cand.get("format_label") or "format unknown",
                    None,
                    cand["id"],
                )
                options.append(
                    {
                        "idx": idx,
                        "provider": "discogs",
                        "id": cand["id"],
                        "label": label,
                        "score": cand.get("score", 0.0),
                    }
                )
                idx += 1
        if not options:
            self._record_skip(directory, "No manual candidates available")
            logger.warning("No manual candidates available for %s (artist hint=%s, album hint=%s)", directory, artist_hint, album_hint)
            return None
        options.sort(key=lambda opt: opt.get("score", 0.0), reverse=True)
        year_hint = f"{dir_year}" if dir_year else "unknown"
        display = self._display_path(directory)
        print(
            f"\nNo automatic metadata match for {display} "
            f"(artist hint: {artist_hint or 'unknown'}, album hint: {album_hint or 'unknown'}, "
            f"{dir_track_count} tracks detected, year hint {year_hint})."
        )
        print("Select a release to apply:")
        for option in options:
            print(f"  {option['idx']}. {option['label']}")
        print("  0. Skip this directory")
        print("  mb:<release-id> or dg:<release-id> to enter an ID manually")
        while True:
            choice = input("Select release (0=skip): ").strip()
            if choice.lower() in {"0", "s", "skip"}:
                self._record_skip(directory, "User skipped manual release selection")
                return None
            manual = self._parse_manual_release_choice(choice)
            if manual:
                return manual
            if not choice.isdigit():
                print("Invalid selection; enter a number or mb:/dg: identifier.")
                continue
            number = int(choice)
            match = next((opt for opt in options if opt["idx"] == number), None)
            if not match:
                print("Selection out of range.")
                continue
            return match["provider"], match["id"]

    def _directory_hints(self, sample_meta: TrackMetadata, directory: Path) -> tuple[Optional[str], Optional[str]]:
        guess = guess_metadata_from_path(sample_meta.path)
        artist_hint = sample_meta.album_artist or sample_meta.artist or guess.artist
        if not artist_hint and directory.parent != directory:
            artist_hint = directory.parent.name
        album_hint = sample_meta.album or guess.album
        if not album_hint:
            names = [directory.name]
            if directory.parent != directory:
                names.insert(0, directory.parent.name)
            if directory.parent.parent != directory.parent:
                names.insert(0, directory.parent.parent.name)
            for name in names:
                if name and not self._looks_like_disc_folder(name):
                    album_hint = name
                    break
            if not album_hint and names:
                album_hint = names[-1]
        return artist_hint, album_hint

    def _apply_discogs_release_details(self, pending_results: list[PendingResult], release_details: dict) -> None:
        if not self.discogs:
            return
        for pending in pending_results:
            self.discogs.apply_release_details(pending.meta, release_details, allow_overwrite=True)
            score = pending.meta.match_confidence or 0.4
            pending.meta.match_confidence = score
            pending.result = LookupResult(pending.meta, score=score)
            pending.matched = True

    def _apply_musicbrainz_release_selection(
        self,
        directory: Path,
        release_id: str,
        pending_results: list[PendingResult],
        force: bool = False,
    ) -> bool:
        self.musicbrainz.release_tracker.register(
            directory,
            release_id,
            self.musicbrainz._fetch_release_tracks,
        )
        self.musicbrainz.release_tracker.remember_release(directory, release_id, 1.0)
        release_data = self.musicbrainz.release_tracker.releases.get(release_id)
        if not release_data:
            return False
        if force:
            release_data.claimed.clear()
        applied = False
        for pending in pending_results:
            if pending.matched and not force:
                applied = True
                continue
            if force:
                pending.matched = False
            if not pending.meta.duration_seconds:
                duration = self.musicbrainz._probe_duration(pending.meta.path)
                if duration:
                    pending.meta.duration_seconds = duration
            guess = guess_metadata_from_path(pending.meta.path)
            release_match = self.musicbrainz.release_tracker.match(directory, guess, pending.meta.duration_seconds)
            if not release_match:
                continue
            lookup = self.musicbrainz.apply_release_match(pending.meta, release_match)
            if lookup:
                pending.result = lookup
                pending.matched = True
                applied = True
        if applied:
            artist = release_data.album_artist if release_data else None
            album = release_data.album_title if release_data else None
            self._persist_directory_release(
                directory,
                "musicbrainz",
                release_id,
                1.0,
                artist_hint=artist,
                album_hint=album,
            )
        return applied

    def _discogs_candidates(self, meta: TrackMetadata) -> list[dict]:
        if not self.discogs:
            return []
        guess = guess_metadata_from_path(meta.path)
        artist_hint = meta.album_artist or meta.artist
        if artist_hint and guess.artist:
            if self._token_overlap_ratio(artist_hint, guess.artist) < 0.35:
                artist_hint = guess.artist
        elif not artist_hint:
            artist_hint = guess.artist
        album_hint = meta.album
        if album_hint and guess.album:
            if self._token_overlap_ratio(album_hint, guess.album) < 0.35:
                album_hint = guess.album
        elif not album_hint:
            album_hint = guess.album
        title_hint = meta.title
        if title_hint and guess.title:
            if self._token_overlap_ratio(title_hint, guess.title) < 0.35:
                title_hint = guess.title
        elif not title_hint:
            title_hint = guess.title
        if not artist_hint and not album_hint and not title_hint:
            return []
        results = self.discogs.search_candidates(artist=artist_hint, album=album_hint, title=title_hint, limit=8)
        candidates = []
        for item in results:
            release_id = item.get("id")
            if release_id is None:
                continue
            details = self.discogs.get_release(int(release_id))
            track_count = item.get("trackcount")
            if track_count is None and details:
                tracklist = details.get("tracklist") or []
                track_count = len([t for t in tracklist if t.get("type_", "track") == "track"])
            formats, disc_count = self._discogs_format_details(item, details)
            artist_name = self._discogs_release_artist(details) or item.get("artist") or item.get("label")
            title_value = (details or {}).get("title") or item.get("title")
            artist_similarity = self._token_overlap_ratio(artist_hint, artist_name)
            album_similarity = self._token_overlap_ratio(album_hint, title_value)
            release_track_total = track_count or (details and len(details.get("tracklist") or [])) or 0
            dir_tracks_val = meta.extra.get("TRACK_TOTAL")
            dir_tracks = None
            if isinstance(dir_tracks_val, str) and dir_tracks_val.isdigit():
                dir_tracks = int(dir_tracks_val)
            if release_track_total and release_track_total >= 3 and dir_tracks:
                if abs(release_track_total - dir_tracks) > max(3, int(0.15 * max(dir_tracks, release_track_total))):
                    continue
            if artist_similarity < 0.2 and album_similarity < 0.2:
                continue
            candidates.append(
                {
                    "id": release_id,
                    "title": title_value,
                    "artist": artist_name,
                    "year": (details or {}).get("year") or item.get("year"),
                    "track_count": track_count,
                    "disc_count": disc_count,
                    "disc_label": self._disc_label(disc_count),
                    "format_label": ", ".join(formats) if formats else None,
                    "formats": formats,
                    "country": (details or {}).get("country") or item.get("country"),
                    "score": item.get("score"),
                    "details": details,
                }
            )
        return candidates

    def _discogs_format_details(self, search_item: dict, release: Optional[dict]) -> tuple[list[str], Optional[int]]:
        entries: list[str] = []
        disc_total = 0
        source_formats = (release or {}).get("formats") or []
        for fmt in source_formats:
            name = fmt.get("name")
            if not name:
                continue
            qty_raw = fmt.get("qty")
            try:
                qty_val = int(qty_raw)
            except (TypeError, ValueError):
                qty_val = 1
            if qty_val <= 0:
                qty_val = 1
            desc = ", ".join(fmt.get("descriptions", []) or [])
            label = f"{qty_val}×{name}" if qty_val > 1 else name
            if desc:
                label = f"{label} ({desc})"
            entries.append(label)
            disc_total += qty_val
        if not entries:
            fmt_field = search_item.get("format")
            if isinstance(fmt_field, list):
                entries.extend([f for f in fmt_field if isinstance(f, str) and f])
            elif isinstance(fmt_field, str) and fmt_field:
                entries.append(fmt_field)
        return entries, (disc_total or None)

    @staticmethod
    def _discogs_release_artist(release: Optional[dict]) -> Optional[str]:
        if not release:
            return None
        artists = release.get("artists") or []
        names: list[str] = []
        for artist in artists:
            name = artist.get("name")
            if not name:
                continue
            base = name.split(" (")[0].strip()
            if base and base not in names:
                names.append(base)
        return ", ".join(names) if names else None

    def _discogs_counts(self, release: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
        if not release:
            return None, None
        track_count = len(release.get("tracklist") or []) or None
        _, disc_total = self._discogs_format_details({}, release)
        return track_count, disc_total

    def _prompt_on_unmatched_release(
        self,
        directory: Path,
        release_key: str,
        unmatched: list[PendingResult],
    ) -> bool:
        provider, release_id = self._split_release_key(release_key)
        display_name = self._display_path(directory)
        sample_titles = []
        for pending in unmatched[:5]:
            title = pending.meta.title or pending.meta.path.name
            sample_titles.append(f"- {title}")
        print(
            f"\n{ANSI_YELLOW if self._use_color else ''}Only {len(unmatched)} file(s) in {display_name} "
            f"failed to match release {provider}:{release_id}.{ANSI_RESET if self._use_color else ''}"
        )
        if sample_titles:
            print("Unmatched tracks:")
            for entry in sample_titles:
                print(f"  {entry}")
        print("Proceed with the matched tracks? [y/N] ", end="")
        choice = input().strip().lower()
        return choice in {"y", "yes"}

    def _delete_directory(self, directory: Path) -> bool:
        try:
            shutil.rmtree(directory)
            logger.info("Deleted directory %s", directory)
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to delete %s: %s", directory, exc)
            return False

    def _archive_directory(self, directory: Path) -> bool:
        if not self.archive_root:
            print("Archive root not configured; set organizer.archive_root in config.")
            return False
        archive_root = self.archive_root
        archive_root.mkdir(parents=True, exist_ok=True)
        relative = None
        for root in self._library_roots:
            try:
                relative = directory.relative_to(root)
                break
            except ValueError:
                continue
        target = archive_root / (relative or directory.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        candidate = target
        counter = 1
        while candidate.exists():
            candidate = target.parent / f"{target.name}-{counter:02d}"
            counter += 1
        try:
            shutil.move(str(directory), str(candidate))
            logger.info("Archived %s -> %s", directory, candidate)
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to archive %s: %s", directory, exc)
            return False

    @staticmethod
    def _disc_label(disc_count: Optional[int]) -> Optional[str]:
        if not disc_count:
            return None
        return f"{disc_count} disc{'s' if disc_count > 1 else ''}"

    def _style(self, text: str, *codes: str) -> str:
        if not text:
            return text
        if not self._use_color or not codes:
            return text
        prefix = "".join(codes)
        return f"{prefix}{text}{ANSI_RESET}"

    def _format_option_label(
        self,
        index: int,
        provider_tag: str,
        artist: str,
        title: str,
        year: str,
        track_count: str,
        disc_label: str,
        format_label: str,
        score: Optional[float],
        release_id: str,
    ) -> str:
        provider = self._style(f"[{provider_tag}]", ANSI_BOLD, ANSI_MAGENTA)
        artist_fmt = self._style(artist, ANSI_BOLD, ANSI_GREEN)
        title_fmt = self._style(title, ANSI_BOLD, ANSI_CYAN)
        year_fmt = self._style(str(year), ANSI_BOLD, ANSI_YELLOW)
        stats = f"{track_count} tracks · {disc_label} · {format_label}".strip()
        stats_fmt = self._style(stats, ANSI_DIM)
        score_fmt = self._style(f"score {score:.2f}", ANSI_DIM) if score is not None else ""
        release_fmt = self._style(release_id, ANSI_DIM)
        sections = [provider, f"{artist_fmt} – {title_fmt}", f"({year_fmt})"]
        sections.append(f"\t{stats_fmt}")
        if score_fmt:
            sections.append(f"\t{score_fmt}")
        sections.append(f"\t{release_fmt}")
        return " ".join(section for section in sections if section)

    def _directory_context(self, directory: Path, files: list[Path]) -> tuple[int, Optional[int]]:
        return len(files), self._infer_year_from_directory(directory)

    def _print_release_selection_summary(
        self,
        directory: Path,
        provider: str,
        release_id: str,
        album: Optional[str],
        artist: Optional[str],
        track_count: Optional[int],
        disc_count: Optional[int],
        pending_results: list[PendingResult],
    ) -> None:
        before_album = None
        before_artist = None
        if pending_results:
            before_album = pending_results[0].meta.album
            before_artist = pending_results[0].meta.album_artist or pending_results[0].meta.artist
        display = self._display_path(directory)
        print(
            f"\nApplying {provider.upper()} release {release_id} to {display}: "
            f"{album or 'unknown album'} – {artist or 'unknown artist'} "
            f"(tracks={track_count or '?'} discs={disc_count or '?'})"
        )
        if before_album or before_artist:
            print(
                f"  Previous tags: album='{before_album or 'unknown'}', artist='{before_artist or 'unknown'}'"
            )

    def _parse_manual_release_choice(self, raw: str) -> Optional[tuple[str, str]]:
        value = raw.strip()
        if not value:
            return None
        lowered = value.lower()
        for prefix in ("mb:", "musicbrainz:"):
            if lowered.startswith(prefix):
                release_id = value[len(prefix) :].strip()
                if release_id:
                    return "musicbrainz", release_id
                return None
        for prefix in ("dg:", "discogs:"):
            if lowered.startswith(prefix):
                release_id = value[len(prefix) :].strip()
                if release_id.isdigit():
                    return "discogs", release_id
                print("Discogs IDs must be numeric.")
                return None
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value.lower()):
            return "musicbrainz", value
        return None

    def _directory_already_processed(self, batch: DirectoryBatch) -> bool:
        if not batch.files:
            return False
        for file_path in batch.files:
            stat = self._safe_stat(file_path)
            if not stat:
                return False
            cached = self.cache.get_processed_file(file_path)
            if not cached:
                return False
            cached_mtime, cached_size, organized_flag = cached
            if (
                cached_mtime != stat.st_mtime_ns
                or cached_size != stat.st_size
                or not organized_flag
            ):
                return False
        return True

    def _infer_year_from_directory(self, directory: Path) -> Optional[int]:
        segments = [directory.name]
        parent_name = directory.parent.name if directory.parent else ""
        if parent_name:
            segments.append(parent_name)
        for segment in segments:
            year = self._parse_year(segment)
            if year:
                return year
        return None

    @staticmethod
    def _parse_year(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        match = re.search(r"(19|20)\d{2}", value)
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    def report_skips(self) -> None:
        with self._skip_lock:
            entries = list(self.skip_reasons.items())
            self.skip_reasons.clear()
        if not entries:
            return
        print("\n\033[33mDirectories skipped:\033[0m")
        for directory, reason in sorted(entries):
            display = self._display_path(directory)
            print(f" - {display}: {reason}")

    def _cached_release_for_directory(self, directory: Path) -> Optional[tuple[str, str, float]]:
        if not self.release_cache_enabled:
            return None
        for key in self._directory_release_keys(directory):
            entry = self.cache.get_directory_release(key)
            if entry:
                provider, release_id, score = entry
                if not str(key).startswith("hint://"):
                    self._persist_directory_release(directory, provider, release_id, score)
                return entry
        return None

    def _directory_release_keys(
        self,
        directory: Path,
        artist_hint: Optional[str] = None,
        album_hint: Optional[str] = None,
    ) -> list[str]:
        keys: list[str] = []
        for path_key in self._release_path_keys(directory):
            if path_key not in keys:
                keys.append(path_key)
        path_artist, path_album = self._path_based_hints(directory)
        final_artist = artist_hint or path_artist
        final_album = album_hint or path_album
        canonical = self._hint_cache_key(final_artist, final_album)
        if canonical and canonical not in keys:
            keys.append(canonical)
        return keys

    def _release_path_keys(self, directory: Path) -> list[str]:
        paths: list[Path] = []
        try:
            resolved = directory.resolve()
        except FileNotFoundError:
            resolved = directory
        paths.append(resolved)
        album_root = self._album_root(directory)
        if album_root != directory:
            try:
                root_resolved = album_root.resolve()
            except FileNotFoundError:
                root_resolved = album_root
            if root_resolved not in paths:
                paths.append(root_resolved)
        return [str(path) for path in paths]

    def _path_based_hints(self, directory: Path) -> tuple[Optional[str], Optional[str]]:
        names: list[str] = []
        current = directory
        for _ in range(3):
            if not current or not current.name:
                break
            names.append(current.name)
            if current.parent == current:
                break
            current = current.parent
        album = next((name for name in names if name and not self._looks_like_disc_folder(name)), names[0] if names else None)
        artist = None
        if len(names) > 1:
            for name in names[1:]:
                if name and not self._looks_like_disc_folder(name):
                    artist = name
                    break
        return artist, album

    def _hint_cache_key(self, artist: Optional[str], album: Optional[str]) -> Optional[str]:
        normalized_album = self._normalize_hint_value(album)
        if not normalized_album:
            return None
        normalized_artist = self._normalize_hint_value(artist) or "unknown"
        return f"hint://{normalized_artist}|{normalized_album}"

    @staticmethod
    def _normalize_hint_value(value: Optional[str]) -> str:
        if not value:
            return ""
        cleaned = unicodedata.normalize("NFKD", value)
        cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
        cleaned = cleaned.lower()
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
        return cleaned.strip()

    def _token_overlap_ratio(self, expected: Optional[str], candidate: Optional[str]) -> float:
        expected_tokens = self._tokenize(expected)
        if not expected_tokens:
            return 0.0
        candidate_tokens = set(self._tokenize(candidate))
        if not candidate_tokens:
            return 0.0
        overlap = sum(1 for token in expected_tokens if token in candidate_tokens)
        return overlap / len(expected_tokens)

    @staticmethod
    def _tokenize(value: Optional[str]) -> list[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            value = " ".join(str(part) for part in value if part)
        else:
            value = str(value)
        cleaned = unicodedata.normalize("NFKD", value)
        cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
        cleaned = cleaned.lower()
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
        return [token for token in cleaned.split() if token]

    def _display_path(self, path: Path | str) -> str:
        try:
            candidate = Path(path).resolve()
        except FileNotFoundError:
            candidate = Path(path)
        for root in self._library_roots:
            try:
                rel = candidate.relative_to(root)
                return str(rel)
            except ValueError:
                continue
        return str(path)

    def _prepare_album_batch(self, batch: DirectoryBatch) -> Optional[DirectoryBatch]:
        directory = batch.directory
        album_root = self._album_root(directory)
        try:
            resolved_root = album_root.resolve()
        except FileNotFoundError:
            resolved_root = album_root
        if resolved_root in self._processed_albums:
            logger.debug("Album %s already processed; skipping %s", album_root, directory)
            return None
        self._processed_albums.add(resolved_root)
        disc_dirs = self._disc_directories(album_root)
        files: list[Path] = []
        seen: set[Path] = set()

        def _add_files(paths: list[Path]) -> None:
            for path in paths:
                if path not in seen:
                    files.append(path)
                    seen.add(path)

        if album_root == directory:
            _add_files(batch.files)
        else:
            root_batch = self.scanner.collect_directory(album_root)
            if root_batch:
                _add_files(root_batch.files)
        for disc_dir in disc_dirs:
            if disc_dir == directory:
                _add_files(batch.files)
            else:
                sub_batch = self.scanner.collect_directory(disc_dir)
                if sub_batch:
                    _add_files(sub_batch.files)
        if not files:
            return None
        return DirectoryBatch(directory=album_root, files=files)

    def _album_root(self, directory: Path) -> Path:
        if self._looks_like_disc_folder(directory.name) and directory.parent != directory:
            return directory.parent
        return directory

    def _disc_directories(self, album_root: Path) -> list[Path]:
        discs: list[Path] = []
        try:
            entries = list(album_root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return discs
        for entry in entries:
            if entry.is_dir() and self._looks_like_disc_folder(entry.name):
                discs.append(entry)
        return sorted(discs)

    def _persist_directory_release(
        self,
        directory: Path,
        provider: str,
        release_id: str,
        score: float,
        artist_hint: Optional[str] = None,
        album_hint: Optional[str] = None,
    ) -> None:
        if not self.release_cache_enabled:
            return
        for key in self._directory_release_keys(directory, artist_hint, album_hint):
            self.cache.set_directory_release(key, provider, release_id, score)

    @staticmethod
    def _looks_like_disc_folder(name: str) -> bool:
        return bool(re.search(r"(?:^|\s)(disc|cd|disk)\s*\d", name, re.IGNORECASE))

    @staticmethod
    def _release_key(provider: str, release_id: str) -> str:
        return f"{provider}:{release_id}"

    @staticmethod
    def _split_release_key(key: str) -> tuple[str, str]:
        if ":" in key:
            provider, release_id = key.split(":", 1)
            return provider, release_id
        return "musicbrainz", key
