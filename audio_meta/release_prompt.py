from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .daemon_types import ReleaseExample
from .prompting import release_url


@dataclass(slots=True)
class ReleasePromptOption:
    idx: int
    provider: str
    release_id: str
    label: str
    score: Optional[float]


def build_release_prompt_options(
    candidates: list[tuple[str, float]],
    release_examples: dict[str, ReleaseExample],
    *,
    split_release_key: Callable[[str], tuple[str, str]],
    parse_year: Callable[[Optional[str]], Any],
    disc_label: Callable[[Optional[int]], Optional[str]],
    format_option_label: Callable[
        [int, str, str, str, str, str, str, str, Optional[float], str], str
    ],
    show_urls: bool,
) -> list[ReleasePromptOption]:
    options: list[ReleasePromptOption] = []
    idx = 1
    for key, score in sorted(candidates, key=lambda x: x[1], reverse=True):
        provider, release_id = split_release_key(key)
        example = release_examples.get(key)
        title = example.title if example else ""
        artist = example.artist if example else ""
        year_val = parse_year(example.date if example else None) or "?"
        year = str(year_val)
        track_count = example.track_total if example else None
        disc_count = example.disc_count if example else None
        formats = example.formats if example else []
        disc_label_val = disc_label(disc_count) or "disc count unknown"
        format_label = ", ".join(formats) if formats else "format unknown"
        tag = "MB" if provider == "musicbrainz" else "DG"
        label = format_option_label(
            idx,
            tag,
            artist or "Unknown Artist",
            title or "Unknown Title",
            year,
            str(track_count or "?"),
            disc_label_val,
            format_label,
            score,
            release_id,
        )
        if show_urls:
            url = release_url(provider, release_id)
            if url:
                label = f"{label}  {url}"
        options.append(
            ReleasePromptOption(
                idx=idx,
                provider=provider,
                release_id=release_id,
                label=label,
                score=score,
            )
        )
        idx += 1
    return options


def append_mb_search_options(
    options: list[ReleasePromptOption],
    mb_candidates: list[dict[str, Any]],
    *,
    show_urls: bool,
    parse_year: Callable[[Optional[str]], Any],
    disc_label: Callable[[Optional[int]], Optional[str]],
    format_option_label: Callable[
        [int, str, str, str, str, str, str, str, Optional[float], str], str
    ],
) -> None:
    seen = {(opt.provider, opt.release_id) for opt in options}
    idx = max((opt.idx for opt in options), default=0) + 1
    for cand in mb_candidates:
        release_id = str(cand.get("id") or "").strip()
        if not release_id:
            continue
        pair = ("musicbrainz", release_id)
        if pair in seen:
            continue
        year_val = parse_year(cand.get("date")) or "?"
        year = str(year_val)
        track_count = str(cand.get("track_total") or "?")
        disc_label_val = disc_label(cand.get("disc_count")) or "disc count unknown"
        format_label = ", ".join(cand.get("formats") or []) or "format unknown"
        score = cand.get("score")
        score_f = float(score) if isinstance(score, (int, float)) else None
        label = format_option_label(
            idx,
            "MB",
            cand.get("artist") or "Unknown Artist",
            cand.get("title") or "Unknown Title",
            year,
            track_count,
            disc_label_val,
            format_label,
            score_f,
            release_id,
        )
        if show_urls:
            url = release_url("musicbrainz", release_id)
            if url:
                label = f"{label}  {url}"
        options.append(
            ReleasePromptOption(
                idx=idx,
                provider="musicbrainz",
                release_id=release_id,
                label=label,
                score=score_f,
            )
        )
        seen.add(pair)
        idx += 1


def append_discogs_search_options(
    options: list[ReleasePromptOption],
    discogs_candidates: list[dict[str, Any]],
    *,
    show_urls: bool,
    format_option_label: Callable[
        [int, str, str, str, str, str, str, str, Optional[float], str], str
    ],
) -> None:
    seen = {(opt.provider, opt.release_id) for opt in options}
    idx = max((opt.idx for opt in options), default=0) + 1
    for cand in discogs_candidates:
        release_id = str(cand.get("id") or "").strip()
        if not release_id:
            continue
        pair = ("discogs", release_id)
        if pair in seen:
            continue
        score = cand.get("score")
        score_f = float(score) if isinstance(score, (int, float)) else None
        label = format_option_label(
            idx,
            "DG",
            cand.get("artist") or "Unknown",
            cand.get("title") or "Unknown Title",
            str(cand.get("year") or "?"),
            str(cand.get("track_count") or "?"),
            cand.get("disc_label") or "disc count unknown",
            cand.get("format_label") or "format unknown",
            score_f,
            release_id,
        )
        if show_urls:
            url = release_url("discogs", release_id)
            if url:
                label = f"{label}  {url}"
        options.append(
            ReleasePromptOption(
                idx=idx,
                provider="discogs",
                release_id=release_id,
                label=label,
                score=score_f,
            )
        )
        seen.add(pair)
        idx += 1
