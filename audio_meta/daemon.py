from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from watchdog.observers import Observer

from .daemon_types import DryRunRecorder, PendingResult, ReleaseExample
from .assignment import best_assignment_max_score
from .match_utils import combine_similarity, duration_similarity, normalize_match_text, normalize_title_for_match, parse_discogs_duration, title_similarity
from .pipeline import (
    ProcessingPipeline,
    TrackEnrichmentContext,
    PlanApplyContext,
    DirectoryContext,
    TrackSkipContext,
)
from .pipeline import TrackSignalContext
from .watchdog_handler import WatchHandler
from .classical import ClassicalHeuristics
from .heuristics import guess_metadata_from_path
from .config import Settings
from .models import TrackMetadata
from .organizer import Organizer
from .providers.discogs import DiscogsClient
from .providers.musicbrainz import LookupResult, MusicBrainzClient, ReleaseMatch
from .scanner import DirectoryBatch, LibraryScanner
from .tagging import TagWriter
from .cache import MetadataCache
from .services import AudioMetaServices
from . import release_home as release_home_logic
from . import deferred as deferred_logic
from . import release_scoring as release_scoring_logic
from . import directory_identity as directory_identity_logic
from .album_batching import AlbumBatcher

logger = logging.getLogger(__name__)

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_MAGENTA = "\033[35m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"


class AudioMetaDaemon:
    def __init__(
        self,
        settings: Settings,
        cache: MetadataCache | None = None,
        scanner: LibraryScanner | None = None,
        musicbrainz: MusicBrainzClient | None = None,
        discogs: DiscogsClient | None = None,
        dry_run_output: Optional[Path] = None,
        interactive: bool = False,
        release_cache_enabled: bool = True,
    ) -> None:
        self.settings = settings
        self.cache = cache or MetadataCache(settings.daemon.cache_path)
        self.services = AudioMetaServices(self)
        self.scanner = scanner or LibraryScanner(settings.library)
        self.musicbrainz = musicbrainz or MusicBrainzClient(settings.providers, cache=self.cache)
        self.discogs = discogs
        self.heuristics = ClassicalHeuristics(settings.classical)
        self.tag_writer = TagWriter()
        self.organizer = Organizer(settings.organizer, settings.library, cache=self.cache)
        self.pipeline = ProcessingPipeline(
            disabled_plugins=set(settings.daemon.pipeline_disable),
            plugin_order=dict(settings.daemon.pipeline_order),
        )
        self.queue: asyncio.Queue[DirectoryBatch] = asyncio.Queue()
        self.observer: Observer | None = None
        self.dry_run_recorder = DryRunRecorder(dry_run_output) if dry_run_output else None
        self.interactive = interactive
        self.release_cache_enabled = release_cache_enabled
        # Always persist "needs user input" decisions so daemon mode can queue them,
        # and interactive scans can replay them after the scan completes.
        self.defer_prompts = True
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
        if self.defer_prompts and self.interactive:
            self._sync_deferred_prompts()

    async def run_scan(self) -> None:
        logger.debug("Starting one-off scan")
        for batch in self.scanner.iter_directories():
            await self.queue.put(batch)
        workers = self._start_workers()
        await self.queue.join()
        await self._stop_workers(workers)
        if self.defer_prompts and self.interactive:
            self._process_deferred_directories()
        self.pipeline.after_scan(self)

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
        handler = WatchHandler(self.queue, self.settings.library.include_extensions, self.scanner)
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
        prepared = self._prepare_album_batch(batch, force_prompt=force_prompt)
        if not prepared:
            return
        batch = prepared
        is_singleton = self._is_singleton_directory(batch)
        logger.debug("Processing directory %s with %d files", batch.directory, len(batch.files))
        directory_hash = self._calculate_directory_hash(batch.directory, batch.files)
        dir_ctx = DirectoryContext(
            daemon=self,
            directory=batch.directory,
            files=list(batch.files),
            force_prompt=force_prompt,
            is_singleton=is_singleton,
            directory_hash=directory_hash,
        )
        if self.pipeline.should_skip_directory(dir_ctx):
            return
        pending_results: list[PendingResult] = []
        release_scores: dict[str, float] = {}
        release_examples: dict[str, ReleaseExample] = {}
        discogs_details: dict[str, dict] = {}
        self.pipeline.analyze_directory(dir_ctx)
        dir_track_count = dir_ctx.dir_track_count
        dir_year = dir_ctx.dir_year
        dir_ctx.pending_results = pending_results
        dir_ctx.release_scores = release_scores
        dir_ctx.release_examples = release_examples
        dir_ctx.discogs_details = discogs_details
        self.pipeline.initialize_directory(dir_ctx)

        for file_path in batch.files:
            meta = TrackMetadata(path=file_path)
            if dir_track_count:
                meta.extra["TRACK_TOTAL"] = str(dir_track_count)
            existing_tags = self._read_existing_tags(meta)
            self.pipeline.extract_signals(
                TrackSignalContext(
                    daemon=self,
                    directory=batch.directory,
                    meta=meta,
                    existing_tags=dict(existing_tags),
                )
            )
            if self.pipeline.should_skip_track(
                TrackSkipContext(daemon=self, directory=batch.directory, file_path=file_path, directory_ctx=dir_ctx)
            ):
                continue
            result = self.pipeline.enrich_track(
                TrackEnrichmentContext(
                    daemon=self,
                    directory=batch.directory,
                    meta=meta,
                    existing_tags=dict(existing_tags),
                )
            )
            pending_results.append(
                PendingResult(
                    meta=meta,
                    result=result,
                    matched=bool(result),
                    existing_tags=dict(existing_tags),
                )
            )
            # Release candidates are aggregated later via pipeline candidate sources.
        if not release_scores and dir_ctx.discogs_release_details:
            self._apply_discogs_release_details(pending_results, dir_ctx.discogs_release_details)

        dir_ctx.pending_results = pending_results
        dir_ctx.release_scores = release_scores
        dir_ctx.release_examples = release_examples
        dir_ctx.discogs_details = discogs_details
        self.pipeline.add_candidates(dir_ctx)

        decision = self.pipeline.decide_release(dir_ctx)
        if decision.should_abort:
            return
        best_release_id = decision.best_release_id
        best_score = decision.best_score
        dir_ctx.best_score = best_score
        dir_ctx.forced_provider = decision.forced_provider
        dir_ctx.forced_release_id = decision.forced_release_id
        dir_ctx.forced_release_score = decision.forced_release_score
        dir_ctx.set_best_release(best_release_id)
        dir_ctx.discogs_release_details = decision.discogs_release_details
        release_outcome = self.pipeline.finalize_release(dir_ctx, decision)
        dir_ctx.applied_provider = release_outcome.provider
        dir_ctx.applied_release_id = release_outcome.release_id
        dir_ctx.album_name = release_outcome.album_name
        dir_ctx.album_artist = release_outcome.album_artist

        self.pipeline.resolve_singleton_release_home(dir_ctx)
        planned = self.pipeline.build_plans(dir_ctx)
        self.pipeline.transform_plans(dir_ctx)
        unmatched_entries = [p for p in pending_results if not p.matched]
        if unmatched_entries and best_release_id and self.interactive:
            self.pipeline.assign_tracks(dir_ctx, force=True)
            unmatched_entries = [p for p in pending_results if not p.matched]
        if unmatched_entries and best_release_id:
            dir_ctx.unmatched = unmatched_entries
            unmatched_decision = self.pipeline.handle_unmatched(dir_ctx)
            if unmatched_decision.should_abort:
                return

        if not planned:
            logger.debug("No actionable files in %s", batch.directory)
            self.pipeline.complete_directory(dir_ctx, applied_plans=False)
            return
        for plan in planned:
            self.pipeline.apply_plan(PlanApplyContext(daemon=self, plan=plan))
        self.pipeline.complete_directory(dir_ctx, applied_plans=True)

    def _enrich_track_default(self, meta: TrackMetadata) -> Optional[LookupResult]:
        result = self.musicbrainz.enrich(meta)
        if result and self.discogs and self._needs_supplement(meta):
            try:
                supplement = self.discogs.supplement(meta)
                if supplement:
                    result = LookupResult(meta, score=max(result.score, supplement.score))
            except Exception:
                logger.exception("Discogs supplement failed for %s", meta.path)
        if not result and self.discogs:
            try:
                result = self.discogs.enrich(meta)
            except Exception:
                logger.exception("Discogs lookup failed for %s", meta.path)
        return result

    def _is_singleton_directory(self, batch: DirectoryBatch) -> bool:
        threshold = max(0, self.settings.organizer.singleton_threshold)
        if threshold <= 0:
            return False
        return len(batch.files) <= threshold

    def _count_audio_files(self, directory: Path) -> int:
        if not directory.exists():
            return 0
        count = 0
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                if self.scanner._should_include(path):
                    count += 1
            except Exception:  # pragma: no cover - defensive
                continue
        return count

    def _find_release_home(
        self,
        release_id: Optional[str],
        current_dir: Path,
        current_count: int,
    ) -> Optional[Path]:
        if not release_id:
            return None
        cached_key = self._release_key("musicbrainz", release_id)
        cached_home = self.cache.get_release_home(cached_key)
        if cached_home:
            raw_path, _, cached_hash = cached_home
            candidate = Path(raw_path)
            if candidate.exists() and candidate != current_dir:
                current_hash = self.cache.get_directory_hash(candidate)
                if cached_hash and current_hash and cached_hash != current_hash:
                    self.cache.delete_release_home(cached_key)
                else:
                    return candidate
            elif not candidate.exists():
                self.cache.delete_release_home(cached_key)
        candidates = self.cache.find_directories_for_release(release_id)
        best_dir: Optional[Path] = None
        best_count = current_count
        for raw in candidates:
            candidate = Path(raw)
            if candidate == current_dir or not candidate.exists():
                continue
            count = self._count_audio_files(candidate)
            if count > best_count:
                best_dir = candidate
                best_count = count
        return best_dir

    def _maybe_set_release_home(
        self,
        release_key: str,
        directory: Path,
        track_count: int,
        directory_hash: Optional[str],
    ) -> None:
        release_home_logic.maybe_set_release_home(
            self,
            release_key,
            directory,
            track_count=track_count,
            directory_hash=directory_hash,
        )

    def _select_singleton_release_home(
        self,
        release_key: str,
        current_dir: Path,
        current_count: int,
        best_release_score: float,
        sample_meta: Optional[TrackMetadata],
    ) -> Optional[Path]:
        return release_home_logic.select_singleton_release_home(
            self,
            release_key,
            current_dir,
            current_count,
            best_release_score,
            sample_meta,
        )

    def _plan_singleton_target(
        self,
        meta: TrackMetadata,
        release_home_dir: Path,
        is_classical: bool,
    ) -> Optional[Path]:
        return release_home_logic.plan_singleton_target(self, meta, release_home_dir, is_classical)

    def _path_under_directory(self, path: Path, directory: Path) -> bool:
        try:
            resolved_path = Path(path).resolve()
            resolved_dir = directory.resolve()
            resolved_path.relative_to(resolved_dir)
            return True
        except (ValueError, FileNotFoundError):
            return False

    def _reprocess_directory(self, directory: Path) -> None:
        batch = self.scanner.collect_directory(directory)
        if not batch:
            return
        try:
            self._process_directory(batch, force_prompt=True)
        except Exception:  # pragma: no cover - logged upstream
            logger.warning("Failed to reprocess %s after singleton move", self._display_path(directory))

    def _schedule_deferred_directory(self, directory: Path, reason: str) -> None:
        deferred_logic.schedule_directory(self, directory, reason)

    def _sync_deferred_prompts(self) -> None:
        deferred_logic.sync_from_cache(self)

    def _process_deferred_directories(self) -> None:
        deferred_logic.process_pending(self)

    def _needs_supplement(self, meta: TrackMetadata) -> bool:
        return not meta.album or not meta.artist or not meta.album_artist

    def _read_existing_tags(self, meta: TrackMetadata) -> dict[str, Optional[str]]:
        tags = self.tag_writer.read_existing_tags(meta)
        if not tags:
            return {}
        cleaned: dict[str, Optional[str]] = {}
        for key, value in tags.items():
            if isinstance(value, str):
                stripped = value.strip()
                cleaned[key] = stripped or None
            else:
                cleaned[key] = value
        return cleaned

    def _apply_tag_hints(self, meta: TrackMetadata, tags: dict[str, Optional[str]]) -> None:
        if not tags:
            return
        def assign(attr: str, key: str) -> None:
            if getattr(meta, attr, None):
                return
            value = self._prepare_tag_value(tags.get(key))
            if value:
                setattr(meta, attr, value)
        assign("title", "title")
        assign("album", "album")
        assign("artist", "artist")
        assign("album_artist", "album_artist")
        assign("composer", "composer")
        assign("genre", "genre")
        assign("work", "work")
        assign("movement", "movement")
        track_number = tags.get("tracknumber") or tags.get("track_number")
        if track_number and "TRACKNUMBER" not in meta.extra:
            cleaned = track_number.strip()
            if "/" in cleaned:
                cleaned = cleaned.split("/", 1)[0]
            if cleaned.isdigit():
                meta.extra["TRACKNUMBER"] = int(cleaned)
        disc_number = tags.get("discnumber") or tags.get("disc_number")
        if disc_number and "DISCNUMBER" not in meta.extra:
            cleaned = disc_number.strip()
            if "/" in cleaned:
                cleaned = cleaned.split("/", 1)[0]
            if cleaned.isdigit():
                meta.extra["DISCNUMBER"] = int(cleaned)

    @staticmethod
    def _prepare_tag_value(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        cleaned = value.strip()
        return cleaned or None

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
        return release_scoring_logic.adjust_release_scores(
            self,
            scores=scores,
            release_examples=release_examples,
            dir_track_count=dir_track_count,
            dir_year=dir_year,
            pending_results=pending_results,
            directory=directory,
            discogs_details=discogs_details,
        )

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

    def _auto_pick_existing_release_home(
        self,
        candidates: list[tuple[str, float]],
        directory: Path,
        current_count: int,
        release_examples: dict[str, ReleaseExample],
    ) -> Optional[str]:
        best_key: Optional[str] = None
        best_rank: Optional[tuple[int, int, float, int, str]] = None
        for key, score in candidates:
            provider, release_id = self._split_release_key(key)
            if provider != "musicbrainz" or not release_id:
                continue
            release_key = self._release_key(provider, release_id)
            release_home, home_count = self._release_home_for_key(release_key, directory, current_count)
            if not release_home or home_count <= 0:
                continue
            example = release_examples.get(key)
            track_total = example.track_total if example else None
            fit = abs(track_total - home_count) if track_total else 10_000
            provider_priority = 0 if provider == "musicbrainz" else 1
            rank = (home_count, -fit, float(score), -provider_priority, key)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_key = key
        return best_key

    def _release_home_for_key(
        self,
        release_key: str,
        current_dir: Path,
        current_count: int,
    ) -> tuple[Optional[Path], int]:
        cached_home = self.cache.get_release_home(release_key)
        if cached_home:
            raw_path, cached_count, cached_hash = cached_home
            candidate = Path(raw_path)
            if candidate.exists() and candidate != current_dir:
                current_hash = self.cache.get_directory_hash(candidate)
                if cached_hash and current_hash and cached_hash != current_hash:
                    self.cache.delete_release_home(release_key)
                else:
                    return candidate, int(cached_count or self._count_audio_files(candidate))
            elif not candidate.exists():
                self.cache.delete_release_home(release_key)
        provider, plain = self._split_release_key(release_key)
        if provider != "musicbrainz":
            return None, 0
        fallback = self._find_release_home(plain, current_dir, current_count)
        if not fallback:
            return None, 0
        return fallback, self._count_audio_files(fallback)

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
                entries.append((normalize_match_text(track.title), track.duration_seconds))
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
                entries.append((normalize_match_text(title), parse_discogs_duration(track.get("duration"))))
            return entries or None
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
            combined = combine_similarity(
                title_similarity(title, track.title),
                duration_similarity(duration, track.duration_seconds),
            )
            if combined is not None and combined > best:
                best = combined
        if best <= 0.0:
            return None
        return best

    def _log_unmatched_candidates(
        self,
        directory: Path,
        release_key: str,
        unmatched: list[PendingResult],
    ) -> None:
        provider, release_id = self._split_release_key(release_key)
        if provider != "musicbrainz" or not release_id:
            return
        release_data = self.musicbrainz.release_tracker.releases.get(release_id)
        if not release_data:
            release_data = self.musicbrainz._fetch_release_tracks(release_id)
            if release_data:
                self.musicbrainz.release_tracker.releases[release_id] = release_data
        if not release_data or not release_data.tracks:
            return
        display = self._display_path(directory)
        for pending in unmatched[:5]:
            meta = pending.meta
            guess = guess_metadata_from_path(meta.path)
            title = meta.title or guess.title or meta.path.stem
            title_norm = normalize_title_for_match(title) or title
            track_number = meta.extra.get("TRACKNUMBER")
            disc_number = meta.extra.get("DISCNUMBER")
            candidates: list[tuple[float, str]] = []
            for track in release_data.tracks:
                score = 0.0
                if meta.musicbrainz_track_id and track.recording_id and meta.musicbrainz_track_id == track.recording_id:
                    score = 1.0
                else:
                    if isinstance(track_number, int) and track.number:
                        diff = abs(track.number - track_number)
                        if diff == 0:
                            score += 0.62
                        elif diff == 1:
                            score += 0.28
                    if isinstance(disc_number, int) and track.disc_number:
                        score += 0.08 if disc_number == track.disc_number else -0.04
                    ratio = title_similarity(title_norm, track.title) or 0.0
                    score += 0.25 * ratio
                    dur_ratio = duration_similarity(meta.duration_seconds, track.duration_seconds)
                    if dur_ratio is not None:
                        score += 0.05 * dur_ratio
                label = f"D{track.disc_number or '?'}:{track.number or '?'} {track.title or '<untitled>'}"
                candidates.append((score, label))
            candidates.sort(key=lambda x: x[0], reverse=True)
            top = ", ".join(f"{label} ({score:.2f})" for score, label in candidates[:3])
            logger.info(
                "Unmatched track in %s: %s -> top candidates: %s",
                display,
                meta.path.name,
                top,
            )

    @staticmethod
    def _safe_stat(path: Path):
        try:
            return path.stat()
        except FileNotFoundError:
            return None

    def _warn_ambiguous_release(
        self,
        directory: Path,
        releases: list[tuple[str, float, Optional[ReleaseExample]]],
        dir_track_count: int,
        dir_year: Optional[int],
    ) -> None:
        release_scoring_logic.warn_ambiguous_release(
            self.services.display_path(directory),
            directory,
            releases,
            dir_track_count,
            dir_year,
            parse_year=self._parse_year,
            split_release_key=self._split_release_key,
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
        tracklist = release_details.get("tracklist") or []
        tracks = [t for t in tracklist if t.get("type_", "track") in (None, "", "track")]
        if not tracks:
            for pending in pending_results:
                self.discogs.apply_release_details(pending.meta, release_details, allow_overwrite=True)
                score = pending.meta.match_confidence or 0.4
                pending.meta.match_confidence = score
                pending.result = LookupResult(pending.meta, score=score)
                pending.matched = True
            return

        assignment_scores: list[list[float]] = []
        for pending in pending_results:
            meta = pending.meta
            if not meta.duration_seconds:
                duration = self.musicbrainz._probe_duration(meta.path)
                if duration:
                    meta.duration_seconds = duration
            guess = guess_metadata_from_path(meta.path)
            track_number = meta.extra.get("TRACKNUMBER")
            if not isinstance(track_number, int):
                track_number = guess.track_number
            title = meta.title or guess.title or meta.path.stem
            title_norm = normalize_title_for_match(title) or title
            row: list[float] = []
            for track in tracks:
                score = 0.0
                position = track.get("position")
                pos_num = None
                if isinstance(position, str):
                    digits = "".join(ch for ch in position if ch.isdigit())
                    pos_num = int(digits) if digits else None
                if isinstance(track_number, int) and pos_num:
                    diff = abs(pos_num - track_number)
                    if diff == 0:
                        score += 0.6
                    elif diff == 1:
                        score += 0.25
                    elif diff == 2:
                        score += 0.1
                if track.get("title"):
                    ratio = title_similarity(title_norm, track.get("title")) or 0.0
                    score += 0.3 * ratio
                dur = parse_discogs_duration(track.get("duration"))
                dur_ratio = duration_similarity(meta.duration_seconds, dur)
                if dur_ratio is not None:
                    score += 0.1 * dur_ratio
                row.append(max(0.0, min(1.0, score)))
            assignment_scores.append(row)

        assignment = best_assignment_max_score(assignment_scores, dummy_score=0.55)
        for pending_index, track_index in enumerate(assignment):
            pending = pending_results[pending_index]
            if track_index is None or track_index >= len(tracks):
                continue
            score = assignment_scores[pending_index][track_index]
            if score < 0.58:
                continue
            track = tracks[track_index]
            self.discogs.apply_release_details_matched(pending.meta, release_details, track, allow_overwrite=True)
            position = track.get("position")
            if "TRACKNUMBER" not in pending.meta.extra and isinstance(position, str):
                digits = "".join(ch for ch in position if ch.isdigit())
                if digits.isdigit():
                    pending.meta.extra["TRACKNUMBER"] = int(digits)
            pending.meta.match_confidence = max(pending.meta.match_confidence or 0.0, 0.35 + score * 0.3)
            pending.result = LookupResult(pending.meta, score=max(pending.result.score if pending.result else 0.0, 0.35 + score * 0.3))
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
        to_assign: list[PendingResult] = []
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
            to_assign.append(pending)

        if to_assign:
            assignment_scores: list[list[float]] = []
            tracks = list(release_data.tracks or [])
            for pending in to_assign:
                meta = pending.meta
                guess = guess_metadata_from_path(meta.path)
                track_number = meta.extra.get("TRACKNUMBER")
                disc_number = meta.extra.get("DISCNUMBER")
                title = meta.title or guess.title or meta.path.stem
                title_norm = normalize_title_for_match(title)
                row: list[float] = []
                for track in tracks:
                    if meta.musicbrainz_track_id and track.recording_id and meta.musicbrainz_track_id == track.recording_id:
                        row.append(1.0)
                        continue
                    score = 0.0
                    if isinstance(track_number, int) and track.number:
                        diff = abs(track.number - track_number)
                        if diff == 0:
                            score += 0.62
                        elif diff == 1:
                            score += 0.28
                        elif diff == 2:
                            score += 0.12
                    if isinstance(disc_number, int) and track.disc_number:
                        if disc_number == track.disc_number:
                            score += 0.08
                        else:
                            score -= 0.04
                    if title_norm and track.title:
                        ratio = title_similarity(title_norm, track.title) or 0.0
                        score += 0.25 * ratio
                    elif title and track.title:
                        ratio = title_similarity(title, track.title) or 0.0
                        score += 0.2 * ratio
                    dur_ratio = duration_similarity(meta.duration_seconds, track.duration_seconds)
                    if dur_ratio is not None:
                        score += 0.05 * dur_ratio
                    row.append(max(0.0, min(1.0, score)))
                assignment_scores.append(row)

            assignment = best_assignment_max_score(assignment_scores, dummy_score=0.62)
            for pending_index, track_index in enumerate(assignment):
                if track_index is None:
                    continue
                if track_index >= len(tracks):
                    continue
                score = assignment_scores[pending_index][track_index]
                if score < 0.63:
                    continue
                track = tracks[track_index]
                release_match = ReleaseMatch(release=release_data, track=track, confidence=score)
                lookup = self.musicbrainz.apply_release_match(to_assign[pending_index].meta, release_match)
                if lookup:
                    to_assign[pending_index].result = lookup
                    to_assign[pending_index].matched = True
                    applied = True
                    release_data.mark_claimed(track.recording_id)
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
        logger.info(
            "Applying %s release %s to %s: %s – %s (tracks=%s discs=%s)",
            provider.upper(),
            release_id,
            display,
            album or "unknown album",
            artist or "unknown artist",
            track_count or "?",
            disc_count or "?",
        )
        if before_album or before_artist:
            logger.debug(
                "Previous tags for %s: album='%s', artist='%s'",
                display,
                before_album or "unknown",
                before_artist or "unknown",
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

    def _calculate_directory_hash(self, directory: Path, files: list[Path]) -> Optional[str]:
        if not files:
            return None
        hasher = hashlib.sha1()
        organizer_marker = (
            f"org:{int(self.organizer.enabled)}|cls:{self.organizer.settings.classical_mixed_strategy}|"
            f"len:{self.organizer.settings.max_filename_length}"
        )
        hasher.update(organizer_marker.encode("utf-8"))
        unique_files = sorted({path.resolve() for path in files})
        for path in unique_files:
            stat = self._safe_stat(path)
            if not stat:
                continue
            try:
                rel = path.relative_to(directory)
            except ValueError:
                rel = path.name
            hasher.update(str(rel).encode("utf-8"))
            hasher.update(str(stat.st_size).encode("utf-8"))
            hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest = hasher.hexdigest()
        return digest or None

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
        return directory_identity_logic.path_based_hints(directory)

    def _hint_cache_key(self, artist: Optional[str], album: Optional[str]) -> Optional[str]:
        return directory_identity_logic.hint_cache_key(artist, album)

    @staticmethod
    def _normalize_hint_value(value: Optional[str]) -> str:
        return directory_identity_logic.normalize_hint_value(value)

    def _token_overlap_ratio(self, expected: Optional[str], candidate: Optional[str]) -> float:
        return directory_identity_logic.token_overlap_ratio(expected, candidate)

    @staticmethod
    def _tokenize(value: Optional[str]) -> list[str]:
        return directory_identity_logic.tokenize(value)

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

    def _prepare_album_batch(self, batch: DirectoryBatch, force_prompt: bool = False) -> Optional[DirectoryBatch]:
        batcher = AlbumBatcher(scanner=self.scanner, processed_albums=self._processed_albums)
        result = batcher.prepare_album_batch(batch, force_prompt=force_prompt)
        if result.already_processed and not force_prompt:
            logger.debug("Album %s already processed; skipping %s", result.album_root, batch.directory)
        return result.batch

    def _album_root(self, directory: Path) -> Path:
        return AlbumBatcher.album_root(directory)

    def _disc_directories(self, album_root: Path) -> list[Path]:
        return AlbumBatcher.disc_directories(album_root)

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
        return directory_identity_logic.looks_like_disc_folder(name)

    @staticmethod
    def _release_key(provider: str, release_id: str) -> str:
        return f"{provider}:{release_id}"

    @staticmethod
    def _split_release_key(key: str) -> tuple[str, str]:
        if ":" in key:
            provider, release_id = key.split(":", 1)
            return provider, release_id
        return "musicbrainz", key
