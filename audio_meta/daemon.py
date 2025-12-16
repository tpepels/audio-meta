from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import unicodedata
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
    def __init__(self, settings: Settings, dry_run_output: Optional[Path] = None, interactive: bool = False) -> None:
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
        self._use_color = sys.stdout.isatty()
        self.skip_reasons: dict[Path, str] = {}
        self._skip_lock = Lock()
        self._processed_albums: set[Path] = set()
        self._library_roots = [root.resolve() for root in settings.library.roots]
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
                await asyncio.get_event_loop().run_in_executor(None, self._process_directory, batch)
            except Exception:  # pragma: no cover - logged and ignored
                logger.exception("Worker %s failed to process %s", worker_id, batch.directory)
            finally:
                self.queue.task_done()

    def _process_directory(self, batch: DirectoryBatch) -> None:
        prepared = self._prepare_album_batch(batch)
        if not prepared:
            return
        batch = prepared
        if self._directory_already_processed(batch):
            logger.debug("Skipping %s; already processed and organized", batch.directory)
            return
        planned: list[PlannedUpdate] = []
        logger.debug("Processing directory %s with %d files", batch.directory, len(batch.files))
        pending_results: list[PendingResult] = []
        release_scores: dict[str, float] = {}
        release_examples: dict[str, ReleaseExample] = {}
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
                    release_examples[cached_release_id] = ReleaseExample(
                        title=release_data.album_title or "",
                        artist=release_data.album_artist or "",
                        date=release_data.release_date,
                        track_total=len(release_data.tracks) if release_data.tracks else None,
                        disc_count=release_data.disc_count or None,
                        formats=list(release_data.formats),
                    )
                release_scores[cached_release_id] = max(release_scores.get(cached_release_id, 0.0), cached_score or 1.0)
            elif provider == "discogs" and self.discogs:
                cached_discogs_release_details = self.discogs.get_release(int(cached_release_id))

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
            pending_results.append(PendingResult(meta=meta, result=result, matched=bool(result)))
            if result and meta.musicbrainz_release_id:
                release_id = meta.musicbrainz_release_id
                release_scores[release_id] = max(release_scores.get(release_id, 0.0), result.score)
                release_data = self.musicbrainz.release_tracker.releases.get(release_id)
                release_examples[release_id] = ReleaseExample(
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
                self._persist_directory_release(
                    batch.directory,
                    "discogs",
                    selection_id,
                    1.0,
                    artist_hint=discogs_artist,
                    album_hint=discogs_release_details.get("title"),
                )
            else:
                applied = self._apply_musicbrainz_release_selection(batch.directory, selection_id, pending_results)
                if not applied:
                    self._record_skip(batch.directory, f"Manual MusicBrainz release {selection_id} did not match tracks")
                    logger.warning("Manual MusicBrainz release %s did not match tracks in %s", selection_id, batch.directory)
                    return
                release_data = self.musicbrainz.release_tracker.releases.get(selection_id)
                if release_data:
                    release_examples[selection_id] = ReleaseExample(
                        title=release_data.album_title or "",
                        artist=release_data.album_artist or "",
                        date=release_data.release_date,
                        track_total=len(release_data.tracks) if release_data.tracks else None,
                        disc_count=release_data.disc_count or None,
                        formats=list(release_data.formats),
                    )
                release_scores[selection_id] = max(release_scores.get(selection_id, 0.0), 1.0)

        release_scores = self._adjust_release_scores(release_scores, release_examples, dir_track_count, dir_year)
        best_release_id = None
        best_score = 0.0
        for rid, score in release_scores.items():
            if score > best_score:
                best_release_id = rid
                best_score = score
        ambiguous_cutoff = 0.05
        if forced_provider == "musicbrainz" and forced_release_id:
            best_release_id = forced_release_id
            best_score = release_scores.get(forced_release_id, forced_release_score or 1.0)
            release_scores[forced_release_id] = best_score
        ambiguous_candidates = [
            (rid, score) for rid, score in release_scores.items() if best_release_id and best_score - score <= ambiguous_cutoff
        ]
        if forced_provider == "musicbrainz" and forced_release_id and best_release_id == forced_release_id:
            ambiguous_candidates = [(forced_release_id, best_score)]
        if best_release_id and len(ambiguous_candidates) > 1:
            if self.interactive:
                sample_meta = pending_results[0].meta if pending_results else None
                selection = self._resolve_release_interactively(
                    batch.directory,
                    ambiguous_candidates,
                    release_examples,
                    sample_meta,
                    dir_track_count,
                    dir_year,
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
                    best_release_id = None
                else:
                    best_release_id = selection_id
                    best_score = next(score for rid, score in ambiguous_candidates if rid == selection_id)
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
        if best_release_id:
            example = release_examples.get(best_release_id)
            album_name = example.title if example else ""
            album_artist = example.artist if example else ""
            self._persist_directory_release(
                batch.directory,
                "musicbrainz",
                best_release_id,
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
            if not any(p.matched for p in pending_results):
                self._record_skip(batch.directory, "No metadata match found for directory")
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

    def _record_skip(self, directory: Path, reason: str) -> None:
        with self._skip_lock:
            self.skip_reasons[directory] = reason

    def _adjust_release_scores(
        self,
        scores: dict[str, float],
        release_examples: dict[str, ReleaseExample],
        dir_track_count: int,
        dir_year: Optional[int],
    ) -> dict[str, float]:
        adjusted: dict[str, float] = {}
        for release_id, base_score in scores.items():
            example = release_examples.get(release_id)
            bonus = 0.0
            release_track_total = example.track_total if example else None
            if dir_track_count and release_track_total:
                ratio = min(dir_track_count, release_track_total) / max(dir_track_count, release_track_total)
                if ratio >= 0.9:
                    bonus += 0.04
                elif ratio >= 0.75:
                    bonus += 0.02
                elif ratio <= 0.5:
                    bonus -= 0.03
            release_year = self._parse_year(example.date if example else None)
            if dir_year and release_year:
                diff = abs(release_year - dir_year)
                if diff == 0:
                    bonus += 0.035
                elif diff == 1:
                    bonus += 0.015
                elif diff >= 3:
                    bonus -= 0.03
            adjusted[release_id] = base_score + bonus
        return adjusted

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
        entries = ", ".join(
            f"{(example.title if example else '') or release_id} "
            f"({release_id}, score={score:.2f}, year={AudioMetaDaemon._parse_year(example.date if example else None) or '?'}, "
            f"tracks={example.track_total if example and example.track_total else '?'})"
            for release_id, score, example in releases
        )
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
        mb_candidates: list[tuple[str, float]],
        release_examples: dict[str, ReleaseExample],
        sample_meta: Optional[TrackMetadata],
        dir_track_count: int,
        dir_year: Optional[int],
    ) -> Optional[tuple[str, str]]:
        options: list[dict] = []
        idx = 1
        for release_id, score in sorted(mb_candidates, key=lambda x: x[1], reverse=True):
            example = release_examples.get(release_id)
            title = example.title if example else ""
            artist = example.artist if example else ""
            year = self._parse_year(example.date if example else None) or "?"
            release = self.musicbrainz.release_tracker.releases.get(release_id)
            track_count = len(release.tracks) if release else example.track_total if example else None
            disc_count = release.disc_count if release and release.disc_count else example.disc_count if example else None
            formats = release.formats if release else example.formats if example else []
            disc_label = self._disc_label(disc_count) or "disc count unknown"
            format_label = ", ".join(formats) if formats else "format unknown"
            label = self._format_option_label(
                idx,
                "MB",
                artist or "Unknown Artist",
                title or "Unknown Title",
                year,
                track_count or "?",
                disc_label,
                format_label,
                score,
                release_id,
            )
            options.append({"idx": idx, "provider": "musicbrainz", "id": release_id, "label": label, "score": score})
            idx += 1
        if sample_meta and self.discogs:
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
            self._record_skip(directory, "No interactive release options available")
            logger.warning("No interactive options available for %s", directory)
            return None
        options.sort(key=lambda opt: opt.get("score", 0.0), reverse=True)
        year_hint = f"{dir_year}" if dir_year else "unknown"
        display = self._display_path(directory)
        print(f"\nAmbiguous release for {display} – {dir_track_count} tracks detected, year hint {year_hint}:")
        for option in options:
            print(f"  {option['idx']}. {option['label']}")
        print("  0. Skip this directory")
        print("  mb:<release-id> or dg:<release-id> to enter an ID manually")
        while True:
            choice = input("Select release (0=skip): ").strip()
            if choice.lower() in {"0", "s", "skip"}:
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
    ) -> bool:
        self.musicbrainz.release_tracker.register(
            directory,
            release_id,
            self.musicbrainz._fetch_release_tracks,
        )
        self.musicbrainz.release_tracker.remember_release(directory, release_id, 1.0)
        release_data = self.musicbrainz.release_tracker.releases.get(release_id)
        applied = False
        for pending in pending_results:
            if pending.matched:
                applied = True
                continue
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
        artist = meta.album_artist or meta.artist or guess.artist
        album = meta.album or guess.album
        title = meta.title or guess.title
        results = self.discogs.search_candidates(artist=artist, album=album, title=title, limit=5)
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
            candidates.append(
                {
                    "id": release_id,
                    "title": (details or {}).get("title") or item.get("title"),
                    "artist": artist_name,
                    "year": (details or {}).get("year") or item.get("year"),
                    "track_count": track_count,
                    "disc_count": disc_count,
                    "disc_label": self._disc_label(disc_count),
                    "format_label": ", ".join(formats) if formats else None,
                    "formats": formats,
                    "country": (details or {}).get("country") or item.get("country"),
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
        if value.isdigit():
            return "discogs", value
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
        for key in self._directory_release_keys(directory):
            entry = self.cache.get_directory_release(key)
            if entry:
                provider, release_id, score = entry
                if not str(key).startswith("hint://"):
                    self._persist_directory_release(directory, provider, release_id, score)
                return entry
        return None

    def _path_within_library(self, path: Path) -> bool:
        try:
            candidate = path.resolve()
        except FileNotFoundError:
            candidate = path
        for root in self._library_roots:
            if candidate == root or root in candidate.parents:
                return True
        return False

    def _directory_release_keys(
        self,
        directory: Path,
        artist_hint: Optional[str] = None,
        album_hint: Optional[str] = None,
    ) -> list[str]:
        keys: list[str] = []
        for key in self._path_chain_keys(directory):
            if key not in keys:
                keys.append(key)
        path_artist, path_album = self._path_based_hints(directory)
        final_artist = artist_hint or path_artist
        final_album = album_hint or path_album
        canonical = self._hint_cache_key(final_artist, final_album)
        if canonical and canonical not in keys:
            keys.append(canonical)
        return keys

    def _path_chain_keys(self, directory: Path) -> list[str]:
        keys: list[str] = []
        try:
            current = directory.resolve()
        except FileNotFoundError:
            current = directory
        while True:
            keys.append(str(current))
            parent = current.parent
            if parent == current or not self._path_within_library(parent):
                break
            current = parent
        return keys

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
        for key in self._directory_release_keys(directory, artist_hint, album_hint):
            self.cache.set_directory_release(key, provider, release_id, score)

    @staticmethod
    def _looks_like_disc_folder(name: str) -> bool:
        return bool(re.search(r"(?:^|\s)(disc|cd|disk)\s*\d", name, re.IGNORECASE))
