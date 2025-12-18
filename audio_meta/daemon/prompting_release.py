from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..daemon_types import ReleaseExample
from ..daemon_types import PendingResult
from ..heuristics import guess_metadata_from_path
from ..models import TrackMetadata
from ..assignment_diagnostics import build_assignment_diagnostics
from ..prompting import invalid_release_choice_message, manual_release_choice_help
from ..release_prompt import (
    ReleasePromptDiagnostics,
    ReleasePromptOption,
    append_discogs_search_options,
    append_mb_search_options,
    build_release_prompt_options,
)
from .prompt_preview import build_prompt_track_preview_lines, select_prompt_preview_files

logger = logging.getLogger(__name__)


class ReleasePrompter:
    def __init__(self, daemon) -> None:
        self.daemon = daemon

    def resolve_release_interactively(
        self,
        directory: Path,
        candidates: list[tuple[str, float]],
        release_examples: dict[str, ReleaseExample],
        sample_meta: Optional[TrackMetadata],
        dir_track_count: int,
        dir_year: Optional[int],
        discogs_details: dict[str, dict],
        *,
        files: Optional[list[Path]] = None,
        pending_results: Optional[list[PendingResult]] = None,
        tag_hints: Optional[dict[str, list[str]]] = None,
        prompt_title: str = "Ambiguous release",
        coverage: Optional[float] = None,
    ) -> Optional[tuple[str, str]]:
        show_urls = bool(getattr(self.daemon.settings.daemon, "prompt_show_urls", True))
        expand_mb = bool(
            getattr(self.daemon.settings.daemon, "prompt_expand_mb_candidates", True)
        )
        mb_search_limit = int(
            getattr(self.daemon.settings.daemon, "prompt_mb_search_limit", 6)
        )
        min_mb_search_score = float(
            getattr(self.daemon.settings.daemon, "prompt_min_mb_search_score", 0.05)
            or 0.0
        )
        if sample_meta is None:
            preview_files = select_prompt_preview_files(
                directory,
                files,
                include_extensions=self.daemon.settings.library.include_extensions,
            )
            if preview_files:
                sample_meta = self.daemon._synthesize_sample_meta(
                    directory, preview_files, dir_track_count
                )

        diagnostics_map = self.build_prompt_assignment_diagnostics(
            candidates=candidates,
            pending_results=pending_results,
            release_examples=release_examples,
            discogs_details=discogs_details,
            dir_track_count=dir_track_count,
            dir_year=dir_year,
            files=files,
            tag_hints=tag_hints,
        )

        options = build_release_prompt_options(
            candidates,
            release_examples,
            split_release_key=self.daemon._split_release_key,
            parse_year=self.daemon._parse_year,
            disc_label=self.daemon._disc_label,
            format_option_label=self.daemon._format_option_label,
            show_urls=show_urls,
            diagnostics=diagnostics_map,
        )
        if (
            sample_meta
            and expand_mb
            and (len(options) < 3 or (coverage is not None and coverage < 0.5))
        ):
            artist_hint, album_hint = self.daemon._directory_hints(sample_meta, directory)
            if artist_hint or album_hint:
                try:
                    mb_candidates = self.daemon.musicbrainz.search_release_candidates(
                        artist_hint, album_hint, limit=mb_search_limit
                    )
                except Exception:  # pragma: no cover
                    mb_candidates = []
                append_mb_search_options(
                    options,
                    mb_candidates,
                    show_urls=show_urls,
                    min_score=min_mb_search_score,
                    parse_year=self.daemon._parse_year,
                    disc_label=self.daemon._disc_label,
                    format_option_label=self.daemon._format_option_label,
                )
        if sample_meta and self.daemon.discogs:
            append_discogs_search_options(
                options,
                self.daemon._discogs_candidates(sample_meta),
                show_urls=show_urls,
                format_option_label=self.daemon._format_option_label,
            )
        if not options:
            self.daemon._record_skip(directory, "No interactive release options available")
            logger.warning("No interactive options available for %s", directory)
            return None

        options.sort(key=lambda opt: float(opt.score or 0.0), reverse=True)
        year_hint = f"{dir_year}" if dir_year else "unknown"
        display = self.daemon._display_path(directory)
        self.daemon.prompt_io.print(
            f"\n{prompt_title} for {display} – {dir_track_count} tracks detected, year hint {year_hint}:"
        )
        if coverage is not None:
            self.daemon.prompt_io.print(
                f"  Coverage: {coverage:.0%} of tracks matched by title/duration"
            )
        self.print_prompt_track_preview(directory, files)
        if not self.daemon.discogs:
            self.daemon.prompt_io.print(
                "  (Discogs disabled; set providers.discogs_token to enable)"
            )
        for option in options:
            self.daemon.prompt_io.print(f"  {option.idx}. {option.label}")
        self.daemon.prompt_io.print("  0. Skip this directory")
        self.daemon.prompt_io.print("  d. Delete this directory")
        self.daemon.prompt_io.print("  a. Archive this directory")
        self.daemon.prompt_io.print("  i. Ignore this directory")
        self.daemon.prompt_io.print(
            manual_release_choice_help(discogs_enabled=bool(self.daemon.discogs))
        )

        while True:
            choice = self.daemon.prompt_io.input("Select release (0=skip): ").strip()
            if choice.lower() in {"0", "s", "skip"}:
                return None
            if choice.lower() in {"d", "del", "delete"}:
                if self.daemon._delete_directory(directory):
                    self.daemon._record_skip(directory, "Directory deleted per user request")
                return None
            if choice.lower() in {"a", "archive"}:
                if not self.daemon._archive_directory(directory):
                    continue
                self.daemon.cache.ignore_directory(directory, "archived")
                return None
            if choice.lower() in {"i", "ignore"}:
                self.daemon.cache.ignore_directory(directory, "user request")
                logger.info("Ignoring %s per user request", self.daemon._display_path(directory))
                return None
            manual = self.daemon._parse_manual_release_choice(choice)
            if manual:
                return manual
            if not choice.isdigit():
                self.daemon.prompt_io.print(
                    invalid_release_choice_message(discogs_enabled=bool(self.daemon.discogs))
                )
                continue
            number = int(choice)
            match = next((opt for opt in options if opt.idx == number), None)
            if not match:
                self.daemon.prompt_io.print("Selection out of range.")
                continue
            return match.provider, match.release_id

    def resolve_unmatched_directory(
        self,
        directory: Path,
        sample_meta: Optional[TrackMetadata],
        dir_track_count: int,
        dir_year: Optional[int],
        *,
        files: Optional[list[Path]] = None,
    ) -> Optional[tuple[str, str]]:
        if sample_meta is None:
            preview_files = select_prompt_preview_files(
                directory,
                files,
                include_extensions=self.daemon.settings.library.include_extensions,
            )
            if preview_files:
                sample_meta = self.daemon._synthesize_sample_meta(
                    directory, preview_files, dir_track_count
                )
        if not sample_meta:
            self.daemon._record_skip(directory, "No sample metadata for manual selection")
            return None

        artist_hint, album_hint = self.daemon._directory_hints(sample_meta, directory)
        show_urls = bool(getattr(self.daemon.settings.daemon, "prompt_show_urls", True))
        mb_search_limit = int(
            getattr(self.daemon.settings.daemon, "prompt_mb_search_limit", 6)
        )
        options: list[ReleasePromptOption] = []
        mb_candidates = self.daemon.musicbrainz.search_release_candidates(
            artist_hint, album_hint, limit=mb_search_limit
        )
        append_mb_search_options(
            options,
            mb_candidates,
            show_urls=show_urls,
            min_score=float(
                getattr(self.daemon.settings.daemon, "prompt_min_mb_search_score", 0.05)
                or 0.0
            ),
            parse_year=self.daemon._parse_year,
            disc_label=self.daemon._disc_label,
            format_option_label=self.daemon._format_option_label,
        )
        if self.daemon.discogs and sample_meta:
            append_discogs_search_options(
                options,
                self.daemon._discogs_candidates(sample_meta),
                show_urls=show_urls,
                format_option_label=self.daemon._format_option_label,
            )
        if not options:
            self.daemon._record_skip(directory, "No manual candidates available")
            logger.warning(
                "No manual candidates available for %s (artist hint=%s, album hint=%s)",
                directory,
                artist_hint,
                album_hint,
            )
            return None

        options.sort(key=lambda opt: float(opt.score or 0.0), reverse=True)
        year_hint = f"{dir_year}" if dir_year else "unknown"
        display = self.daemon._display_path(directory)
        self.daemon.prompt_io.print(
            f"\nNo automatic metadata match for {display} "
            f"(artist hint: {artist_hint or 'unknown'}, album hint: {album_hint or 'unknown'}, "
            f"{dir_track_count} tracks detected, year hint {year_hint})."
        )
        self.print_prompt_track_preview(directory, files)
        self.daemon.prompt_io.print("Select a release to apply:")
        for option in options:
            self.daemon.prompt_io.print(f"  {option.idx}. {option.label}")
        self.daemon.prompt_io.print("  0. Skip this directory")
        self.daemon.prompt_io.print(
            manual_release_choice_help(discogs_enabled=bool(self.daemon.discogs))
        )

        while True:
            choice = self.daemon.prompt_io.input("Select release (0=skip): ").strip()
            if choice.lower() in {"0", "s", "skip"}:
                self.daemon._record_skip(directory, "User skipped manual release selection")
                return None
            manual = self.daemon._parse_manual_release_choice(choice)
            if manual:
                return manual
            if not choice.isdigit():
                self.daemon.prompt_io.print(
                    invalid_release_choice_message(discogs_enabled=bool(self.daemon.discogs))
                )
                continue
            number = int(choice)
            match = next((opt for opt in options if opt.idx == number), None)
            if not match:
                self.daemon.prompt_io.print("Selection out of range.")
                continue
            return match.provider, match.release_id

    def prompt_preview_count(self) -> int:
        raw = getattr(self.daemon.settings.daemon, "prompt_preview_tracks", 3)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 3
        return max(0, min(value, 20))

    def print_prompt_track_preview(
        self, directory: Path, files: Optional[list[Path]]
    ) -> None:
        lines = build_prompt_track_preview_lines(
            directory,
            files,
            include_extensions=self.daemon.settings.library.include_extensions,
            limit=self.prompt_preview_count(),
            read_existing_tags=self.daemon._read_existing_tags,
            apply_tag_hints=self.daemon._apply_tag_hints,
        )
        if not lines:
            return
        self.daemon.prompt_io.print("  Sample tracks:")
        for line in lines:
            self.daemon.prompt_io.print(f"    - {line}")

    def build_prompt_assignment_diagnostics(
        self,
        *,
        candidates: list[tuple[str, float]],
        pending_results: Optional[list[PendingResult]],
        release_examples: dict[str, ReleaseExample],
        discogs_details: dict[str, dict],
        dir_track_count: int,
        dir_year: Optional[int],
        files: Optional[list[Path]],
        tag_hints: Optional[dict[str, list[str]]],
    ) -> dict[str, ReleasePromptDiagnostics]:
        if not candidates:
            return {}
        if pending_results:
            return build_assignment_diagnostics(
                self.daemon,
                candidates=candidates,
                pending_results=pending_results,
                release_examples=release_examples,
                discogs_details=discogs_details,
                dir_track_count=dir_track_count,
                dir_year=dir_year,
                tag_hints=tag_hints,
            )

        # Fallback: if we don't have pending_results (e.g. fully-cached/skipped runs),
        # estimate diagnostics from the prompt preview files.
        if not files:
            return {}
        preview_files = select_prompt_preview_files(
            files[0].parent,
            files,
            include_extensions=self.daemon.settings.library.include_extensions,
        )
        synthetic: list[PendingResult] = []
        for file_path in preview_files:
            meta = TrackMetadata(path=file_path)
            tags = self.daemon._read_existing_tags(meta)
            self.daemon._apply_tag_hints(meta, tags)
            meta.title = meta.title or guess_metadata_from_path(file_path).title or file_path.stem
            synthetic.append(PendingResult(meta=meta, result=None, matched=False, existing_tags=dict(tags)))

        return build_assignment_diagnostics(
            self.daemon,
            candidates=candidates,
            pending_results=synthetic,
            release_examples=release_examples,
            discogs_details=discogs_details,
            dir_track_count=dir_track_count,
            dir_year=dir_year,
            tag_hints=tag_hints,
        )
