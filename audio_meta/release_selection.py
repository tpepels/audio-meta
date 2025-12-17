from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .daemon_types import PendingResult, ReleaseExample

if TYPE_CHECKING:  # pragma: no cover
    from .daemon import AudioMetaDaemon

logger = logging.getLogger(__name__)


@dataclass
class ReleaseDecision:
    best_release_id: Optional[str]
    best_score: float
    ambiguous_candidates: list[tuple[str, float]]
    coverage: float
    forced_provider: Optional[str]
    forced_release_id: Optional[str]
    forced_release_score: float
    discogs_release_details: Optional[dict]
    release_summary_printed: bool
    should_abort: bool = False


def decide_release(
    daemon: "AudioMetaDaemon",
    directory: Path,
    file_count: int,
    is_singleton: bool,
    dir_track_count: int,
    dir_year: Optional[int],
    pending_results: list[PendingResult],
    release_scores: dict[str, float],
    release_examples: dict[str, ReleaseExample],
    discogs_details: dict[str, dict],
    forced_provider: Optional[str],
    forced_release_id: Optional[str],
    forced_release_score: float,
    force_prompt: bool,
    release_summary_printed: bool,
) -> ReleaseDecision:
    discogs_release_details = None
    release_scores, coverage_map = daemon._adjust_release_scores(
        release_scores,
        release_examples,
        dir_track_count,
        dir_year,
        pending_results,
        directory,
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
        key = daemon._release_key(forced_provider, forced_release_id)
        best_release_id = key
        best_score = release_scores.get(key, forced_release_score or 1.0)
        release_scores[key] = best_score
    ambiguous_candidates = [
        (rid, score) for rid, score in release_scores.items() if best_release_id and best_score - score <= ambiguous_cutoff
    ]
    if forced_provider and forced_release_id and best_release_id == daemon._release_key(forced_provider, forced_release_id):
        forced_key = daemon._release_key(forced_provider, forced_release_id)
        ambiguous_candidates = [(forced_key, best_score)]
    if len(ambiguous_candidates) > 1:
        auto_pick = daemon._auto_pick_equivalent_release(
            ambiguous_candidates,
            release_examples,
            discogs_details,
        )
        if auto_pick:
            best_release_id = auto_pick
            best_score = release_scores.get(auto_pick, best_score)
            ambiguous_candidates = [(auto_pick, best_score)]
    if (
        is_singleton
        and len(ambiguous_candidates) > 1
        and not (forced_provider and forced_release_id)
    ):
        home_pick = daemon._auto_pick_existing_release_home(
            ambiguous_candidates,
            directory,
            file_count,
            release_examples,
        )
        if home_pick:
            best_release_id = home_pick
            best_score = release_scores.get(home_pick, best_score)
            ambiguous_candidates = [(home_pick, best_score)]
    if best_release_id and len(ambiguous_candidates) > 1 and dir_track_count:
        fit_pick = _auto_pick_best_fit_release(
            daemon,
            ambiguous_candidates,
            directory,
            file_count,
            dir_track_count,
            release_examples,
        )
        if fit_pick:
            best_release_id = fit_pick
            best_score = release_scores.get(fit_pick, best_score)
            ambiguous_candidates = [(fit_pick, best_score)]
    coverage_threshold = 0.0 if is_singleton else 0.7
    coverage = coverage_map.get(best_release_id, 1.0) if best_release_id else 1.0
    if best_release_id and coverage < coverage_threshold:
        if daemon.defer_prompts and not force_prompt and not daemon._processing_deferred:
            daemon._schedule_deferred_directory(directory, "low_coverage")
            return ReleaseDecision(
                best_release_id=best_release_id,
                best_score=best_score,
                ambiguous_candidates=ambiguous_candidates,
                coverage=coverage,
                forced_provider=forced_provider,
                forced_release_id=forced_release_id,
                forced_release_score=forced_release_score,
                discogs_release_details=discogs_release_details,
                release_summary_printed=release_summary_printed,
                should_abort=True,
            )
        if daemon.interactive:
            logger.warning(
                "Release %s matches only %.0f%% of tracks in %s; confirmation required",
                best_release_id,
                coverage * 100,
                daemon._display_path(directory),
            )
            top_candidates = sorted(release_scores.items(), key=lambda x: x[1], reverse=True)[:5]
            sample_meta = pending_results[0].meta if pending_results else None
            if sample_meta and dir_track_count:
                sample_meta.extra.setdefault("TRACK_TOTAL", str(dir_track_count))
            selection = daemon._resolve_release_interactively(
                directory,
                top_candidates,
                release_examples,
                sample_meta,
                dir_track_count,
                dir_year,
                discogs_details,
            )
            if selection is None:
                daemon._record_skip(directory, "User skipped low-coverage release selection")
                logger.warning("Skipping %s due to low coverage", directory)
                return ReleaseDecision(
                    best_release_id=best_release_id,
                    best_score=best_score,
                    ambiguous_candidates=ambiguous_candidates,
                    coverage=coverage,
                    forced_provider=forced_provider,
                    forced_release_id=forced_release_id,
                    forced_release_score=forced_release_score,
                    discogs_release_details=discogs_release_details,
                    release_summary_printed=release_summary_printed,
                    should_abort=True,
                )
            provider, selection_id = selection
            best_release_id = daemon._release_key(provider, selection_id)
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
                daemon._display_path(directory),
            )
            daemon._record_skip(directory, "Low coverage release match")
            return ReleaseDecision(
                best_release_id=best_release_id,
                best_score=best_score,
                ambiguous_candidates=ambiguous_candidates,
                coverage=coverage,
                forced_provider=forced_provider,
                forced_release_id=forced_release_id,
                forced_release_score=forced_release_score,
                discogs_release_details=discogs_release_details,
                release_summary_printed=release_summary_printed,
                should_abort=True,
            )
    if best_release_id and len(ambiguous_candidates) > 1:
        if daemon.defer_prompts and not force_prompt and not daemon._processing_deferred:
            daemon._schedule_deferred_directory(directory, "ambiguous_release")
            return ReleaseDecision(
                best_release_id=best_release_id,
                best_score=best_score,
                ambiguous_candidates=ambiguous_candidates,
                coverage=coverage,
                forced_provider=forced_provider,
                forced_release_id=forced_release_id,
                forced_release_score=forced_release_score,
                discogs_release_details=discogs_release_details,
                release_summary_printed=release_summary_printed,
                should_abort=True,
            )
        if daemon.interactive:
            sample_meta = pending_results[0].meta if pending_results else None
            if sample_meta and dir_track_count:
                sample_meta.extra.setdefault("TRACK_TOTAL", str(dir_track_count))
            selection = daemon._resolve_release_interactively(
                directory,
                ambiguous_candidates,
                release_examples,
                sample_meta,
                dir_track_count,
                dir_year,
                discogs_details,
            )
            if selection is None:
                daemon._record_skip(directory, "User skipped ambiguous release selection")
                logger.warning("Skipping %s per user choice", directory)
                return ReleaseDecision(
                    best_release_id=best_release_id,
                    best_score=best_score,
                    ambiguous_candidates=ambiguous_candidates,
                    coverage=coverage,
                    forced_provider=forced_provider,
                    forced_release_id=forced_release_id,
                    forced_release_score=forced_release_score,
                    discogs_release_details=discogs_release_details,
                    release_summary_printed=release_summary_printed,
                    should_abort=True,
                )
            provider, selection_id = selection
            if provider == "discogs":
                if not daemon.discogs:
                    daemon._record_skip(directory, "Discogs provider unavailable for manual selection")
                    logger.warning("Discogs provider unavailable; cannot use selection for %s", directory)
                    return ReleaseDecision(
                        best_release_id=best_release_id,
                        best_score=best_score,
                        ambiguous_candidates=ambiguous_candidates,
                        coverage=coverage,
                        forced_provider=forced_provider,
                        forced_release_id=forced_release_id,
                        forced_release_score=forced_release_score,
                        discogs_release_details=discogs_release_details,
                        release_summary_printed=release_summary_printed,
                        should_abort=True,
                    )
                discogs_release_details = daemon.discogs.get_release(int(selection_id))
                if not discogs_release_details:
                    daemon._record_skip(directory, f"Failed to load Discogs release {selection_id}")
                    logger.warning("Failed to load Discogs release %s; skipping %s", selection_id, directory)
                    return ReleaseDecision(
                        best_release_id=best_release_id,
                        best_score=best_score,
                        ambiguous_candidates=ambiguous_candidates,
                        coverage=coverage,
                        forced_provider=forced_provider,
                        forced_release_id=forced_release_id,
                        forced_release_score=forced_release_score,
                        discogs_release_details=discogs_release_details,
                        release_summary_printed=release_summary_printed,
                        should_abort=True,
                    )
                discogs_artist = daemon._discogs_release_artist(discogs_release_details)
                daemon._persist_directory_release(
                    directory,
                    "discogs",
                    selection_id,
                    1.0,
                    artist_hint=discogs_artist,
                    album_hint=discogs_release_details.get("title"),
                )
                daemon._print_release_selection_summary(
                    directory,
                    "discogs",
                    selection_id,
                    discogs_release_details.get("title"),
                    discogs_artist,
                    discogs_release_details.get("track_count"),
                    discogs_release_details.get("disc_count"),
                    pending_results,
                )
                release_summary_printed = True
                key = daemon._release_key("discogs", selection_id)
                discogs_details[key] = discogs_release_details
                best_release_id = key
                best_score = 1.0
                release_scores[key] = 1.0
            else:
                key = daemon._release_key("musicbrainz", selection_id)
                best_release_id = key
                best_score = next(score for rid, score in ambiguous_candidates if rid == key)
                daemon._apply_musicbrainz_release_selection(
                    directory,
                    selection_id,
                    pending_results,
                    force=True,
                )
                release_data = daemon.musicbrainz.release_tracker.releases.get(selection_id)
                if release_data:
                    daemon._print_release_selection_summary(
                        directory,
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
            daemon._warn_ambiguous_release(
                directory,
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
            daemon._record_skip(directory, "Ambiguous release matches in non-interactive mode")
            return ReleaseDecision(
                best_release_id=best_release_id,
                best_score=best_score,
                ambiguous_candidates=ambiguous_candidates,
                coverage=coverage,
                forced_provider=forced_provider,
                forced_release_id=forced_release_id,
                forced_release_score=forced_release_score,
                discogs_release_details=discogs_release_details,
                release_summary_printed=release_summary_printed,
                should_abort=True,
            )

    return ReleaseDecision(
        best_release_id=best_release_id,
        best_score=best_score,
        ambiguous_candidates=ambiguous_candidates,
        coverage=coverage,
        forced_provider=forced_provider,
        forced_release_id=forced_release_id,
        forced_release_score=forced_release_score,
        discogs_release_details=discogs_release_details,
        release_summary_printed=release_summary_printed,
        should_abort=False,
    )


def _auto_pick_best_fit_release(
    daemon: "AudioMetaDaemon",
    candidates: list[tuple[str, float]],
    directory: Path,
    file_count: int,
    dir_track_count: int,
    release_examples: dict[str, ReleaseExample],
) -> Optional[str]:
    if not dir_track_count or len(candidates) < 2:
        return None

    def _fit_ratio(a: Optional[int], b: Optional[int]) -> float:
        if not a or not b:
            return 0.0
        return min(a, b) / max(a, b)

    ranked: list[tuple[float, float, str]] = []
    for key, score in candidates:
        example = release_examples.get(key)
        track_total = example.track_total if example else None
        fit = _fit_ratio(track_total, dir_track_count)
        provider, release_id = daemon._split_release_key(key)
        if provider == "musicbrainz" and release_id:
            release_key = daemon._release_key(provider, release_id)
            _, home_count = daemon._release_home_for_key(release_key, directory, file_count)
            fit = max(fit, _fit_ratio(home_count, dir_track_count))
        ranked.append((fit, float(score), key))

    ranked.sort(reverse=True)
    best_fit, best_score, best_key = ranked[0]
    second_fit, _, _ = ranked[1]
    if best_fit < 0.92:
        return None
    if best_fit - second_fit < 0.07:
        return None
    if best_score < 0.5:
        return None
    return best_key
