from __future__ import annotations

import logging
import unicodedata
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .daemon_types import PendingResult, ReleaseExample
from .match_utils import normalize_match_text

if TYPE_CHECKING:
    from .daemon import AudioMetaDaemon

logger = logging.getLogger(__name__)


def adjust_release_scores(
    daemon: "AudioMetaDaemon",
    scores: dict[str, float],
    release_examples: dict[str, ReleaseExample],
    dir_track_count: int,
    dir_year: Optional[int],
    pending_results: list[PendingResult],
    tag_hints: Optional[dict[str, list[str]]],
    directory: Path,
    discogs_details: dict[str, dict],
) -> tuple[dict[str, float], dict[str, float]]:
    adjusted: dict[str, float] = {}
    coverage_map: dict[str, float] = {}
    dir_name = (directory.name or "").lower()
    dir_flags = {
        flag
        for flag in ("deluxe", "expanded", "bonus", "piano", "live", "remaster")
        if flag in dir_name
    }
    for key, base_score in scores.items():
        example = release_examples.get(key)
        bonus = 0.0
        release_track_total = example.track_total if example else None
        if dir_track_count and release_track_total:
            ratio = min(dir_track_count, release_track_total) / max(
                dir_track_count, release_track_total
            )
            if ratio >= 0.95:
                bonus += 0.08
            elif ratio >= 0.85:
                bonus += 0.05
            elif ratio >= 0.7:
                bonus += 0.02
            elif ratio <= 0.4:
                bonus -= 0.12
            elif ratio <= 0.55:
                bonus -= 0.07
        release_year = daemon._parse_year(example.date if example else None)
        if dir_year and release_year:
            diff = abs(release_year - dir_year)
            if diff == 0:
                bonus += 0.035
            elif diff == 1:
                bonus += 0.015
            elif diff >= 3:
                bonus -= 0.03
        if dir_flags and example:
            title_tokens = normalize_match_text(example.title or "")
            if any(flag in title_tokens for flag in dir_flags):
                bonus += 0.02
        bonus += _tag_overlap_bonus(
            daemon, example, pending_results, tag_hints, directory
        )
        extra_bonus, coverage = _release_match_quality(
            daemon,
            key,
            pending_results,
            discogs_details,
            release_examples,
        )
        if (
            dir_flags
            and key.startswith("musicbrainz:")
            and release_track_total
            and release_track_total >= dir_track_count
        ):
            provider, release_id = daemon._split_release_key(key)
            if provider == "musicbrainz" and release_id:
                release_data = daemon.musicbrainz.release_tracker.releases.get(
                    release_id
                )
                if release_data and release_data.tracks:
                    track_text = " ".join(
                        t.title or "" for t in release_data.tracks[:50]
                    ).lower()
                    if "piano" in dir_flags and "piano" in track_text:
                        bonus += 0.02
                    if "bonus" in dir_flags and "bonus" in track_text:
                        bonus += 0.02
        bonus += extra_bonus
        coverage_map[key] = coverage
        adjusted[key] = base_score + bonus
    return adjusted, coverage_map


def warn_ambiguous_release(
    display: str,
    directory: Path,
    releases: list[tuple[str, float, Optional[ReleaseExample]]],
    dir_track_count: int,
    dir_year: Optional[int],
    *,
    parse_year,
    split_release_key,
) -> None:
    hint = (
        f"{dir_track_count} audio files" if dir_track_count else "unknown track count"
    )
    if dir_year:
        hint = f"{hint}; year hint {dir_year}"
    entry_texts = []
    for key, score, example in releases:
        provider, release_id = split_release_key(key)
        entry_texts.append(
            f"[{provider[:2].upper()}] {(example.title if example else '') or release_id} "
            f"({release_id}, score={score:.2f}, year={parse_year(example.date if example else None) or '?'}, "
            f"tracks={example.track_total if example and example.track_total else '?'})"
        )
    entries = ", ".join(entry_texts)
    logger.warning(
        "Ambiguous release detection for %s (%s) – multiple albums scored similarly: %s. "
        "Skipping this directory; adjust tags or split folders, then rerun.",
        display,
        hint,
        entries,
    )


def _tag_overlap_bonus(
    daemon: "AudioMetaDaemon",
    example: Optional[ReleaseExample],
    pending_results: list[PendingResult],
    tag_hints: Optional[dict[str, list[str]]],
    directory: Path,
) -> float:
    if not example:
        return 0.0
    bonus = 0.0
    first_meta = pending_results[0].meta if pending_results else None
    tag_artist, tag_album, tag_composer, tag_work = _aggregated_tag_hints(
        pending_results, tag_hints
    )
    release_artist = example.artist or None
    release_album = example.title or None
    primary_artist = tag_artist or (
        first_meta.album_artist or first_meta.artist if first_meta else None
    )
    primary_album = tag_album or (first_meta.album if first_meta else None)
    if primary_artist:
        weight = 1.2 if tag_artist else 0.8
        bonus += _weighted_overlap(
            daemon._token_overlap_ratio(primary_artist, release_artist), weight
        )
    if primary_album:
        weight = 1.2 if tag_album else 0.8
        bonus += _weighted_overlap(
            daemon._token_overlap_ratio(primary_album, release_album), weight
        )
    if tag_composer:
        bonus += _positive_weighted_overlap(
            daemon._token_overlap_ratio(tag_composer, release_artist), 0.9
        )
    if tag_work:
        bonus += _positive_weighted_overlap(
            daemon._token_overlap_ratio(tag_work, release_album), 0.8
        )
    hint_artist, hint_album = daemon._path_based_hints(directory)
    bonus += _weighted_overlap(
        daemon._token_overlap_ratio(hint_artist, release_artist), 0.5
    )
    bonus += _weighted_overlap(
        daemon._token_overlap_ratio(hint_album, release_album), 0.5
    )
    return max(-0.05, min(0.05, bonus))


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


def _weighted_overlap(ratio: Optional[float], weight: float) -> float:
    if weight <= 0:
        return 0.0
    return _overlap_delta(ratio) * weight


def _positive_overlap_delta(ratio: Optional[float]) -> float:
    if ratio is None:
        return 0.0
    if ratio >= 0.75:
        return 0.02
    if ratio >= 0.6:
        return 0.01
    return 0.0


def _positive_weighted_overlap(ratio: Optional[float], weight: float) -> float:
    if weight <= 0:
        return 0.0
    return _positive_overlap_delta(ratio) * weight


def _aggregated_tag_hints(
    pending_results: list[PendingResult],
    tag_hints: Optional[dict[str, list[str]]],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    artist_values: list[str] = []
    album_values: list[str] = []
    composer_values: list[str] = []
    work_values: list[str] = []
    for pending in pending_results:
        tags = pending.existing_tags
        if not tags:
            continue
        for candidate in (tags.get("album_artist"), tags.get("artist")):
            if candidate:
                artist_values.append(candidate)
                break
        album_candidate = tags.get("album")
        if album_candidate:
            album_values.append(album_candidate)
        composer_candidate = tags.get("composer")
        if composer_candidate:
            composer_values.append(composer_candidate)
        work_candidate = tags.get("work")
        if work_candidate:
            work_values.append(work_candidate)
    if tag_hints:
        artist_values.extend(tag_hints.get("artist") or [])
        album_values.extend(tag_hints.get("album") or [])
        composer_values.extend(tag_hints.get("composer") or [])
        work_values.extend(tag_hints.get("work") or [])
    artist = _dominant_value_consensus(artist_values)
    album = _dominant_value_consensus(album_values)
    composer = _dominant_value_consensus(composer_values)
    work = _dominant_value_consensus(work_values)
    return artist, album, composer, work


def _dominant_value(candidates: list[str]) -> Optional[str]:
    counter: Counter[str] = Counter()
    canonical_map: dict[str, str] = {}
    for candidate in candidates:
        cleaned = _clean_tag_hint(candidate)
        if not cleaned:
            continue
        canonical = cleaned.lower()
        counter[canonical] += 1
        canonical_map.setdefault(canonical, cleaned)
    if not counter:
        return None
    canonical, _ = counter.most_common(1)[0]
    return canonical_map.get(canonical)


def _dominant_value_consensus(
    candidates: list[str],
    *,
    min_count: int = 2,
    min_ratio: float = 0.7,
) -> Optional[str]:
    """
    Return a dominant value only if it is consistent enough across tracks.

    This avoids over-weighting tags like composer/performer when a directory
    contains mixed works/artists (e.g., compilations, mis-tagged files).
    """
    if not candidates:
        return None
    counter: Counter[str] = Counter()
    canonical_map: dict[str, str] = {}
    total = 0
    for candidate in candidates:
        cleaned = _clean_tag_hint(candidate)
        if not cleaned:
            continue
        total += 1
        canonical = cleaned.lower()
        counter[canonical] += 1
        canonical_map.setdefault(canonical, cleaned)
    if not counter:
        return None
    canonical, freq = counter.most_common(1)[0]
    if total >= min_count and (freq / total) < min_ratio:
        return None
    return canonical_map.get(canonical)


def _clean_tag_hint(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    normalized = unicodedata.normalize("NFKD", cleaned)
    return normalized.strip()


def _release_match_quality(
    daemon: "AudioMetaDaemon",
    key: str,
    pending_results: list[PendingResult],
    discogs_details: dict[str, dict],
    release_examples: dict[str, ReleaseExample],
) -> tuple[float, float]:
    provider, release_id = daemon._split_release_key(key)
    if provider != "musicbrainz":
        return 0.0, 1.0
    release_data = daemon.musicbrainz.release_tracker.releases.get(release_id)
    if not release_data:
        release_data = daemon.musicbrainz._fetch_release_tracks(release_id)
        if release_data:
            daemon.musicbrainz.release_tracker.releases[release_id] = release_data
    if release_data:
        example = release_examples.get(key)
        track_total = len(release_data.tracks) if release_data.tracks else None
        if not example:
            release_examples[key] = ReleaseExample(
                provider="musicbrainz",
                title=release_data.album_title or "",
                artist=release_data.album_artist or "",
                date=release_data.release_date,
                track_total=track_total,
                disc_count=release_data.disc_count or None,
                formats=list(release_data.formats),
            )
        else:
            if not example.track_total:
                example.track_total = track_total
            if not example.disc_count:
                example.disc_count = release_data.disc_count or None
            if not example.formats and release_data.formats:
                example.formats = list(release_data.formats)
    if not release_data or not release_data.tracks:
        return 0.0, 0.0
    total = 0.0
    count = 0
    for pending in pending_results:
        track_score = daemon._match_pending_to_release(pending.meta, release_data)
        if track_score is not None:
            total += track_score
            count += 1
    coverage = count / len(pending_results) if pending_results else 0.0
    if not count:
        return 0.0, coverage
    avg = total / count
    return min(0.08, avg * 0.08), coverage
