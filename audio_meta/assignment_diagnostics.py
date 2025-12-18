from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from .daemon_types import PendingResult, ReleaseExample
from .match_utils import combine_similarity, duration_similarity, title_similarity
from .release_prompt import ReleasePromptDiagnostics

if TYPE_CHECKING:  # pragma: no cover
    from .daemon import AudioMetaDaemon


@dataclass(slots=True)
class AssignmentDiagnosticsConfig:
    match_threshold: float = 0.75
    strong_threshold: float = 0.9
    overlap_mismatch_threshold: float = 0.2


def build_assignment_diagnostics(
    daemon: "AudioMetaDaemon",
    *,
    candidates: list[tuple[str, float]],
    pending_results: list[PendingResult],
    release_examples: dict[str, ReleaseExample],
    discogs_details: dict[str, dict],
    dir_track_count: int,
    dir_year: Optional[int],
    tag_hints: Optional[dict[str, list[str]]],
    config: AssignmentDiagnosticsConfig | None = None,
) -> dict[str, ReleasePromptDiagnostics]:
    if not pending_results or not candidates:
        return {}
    cfg = config or AssignmentDiagnosticsConfig()

    def _consensus(values: Optional[list[str]]) -> Optional[str]:
        if not values:
            return None
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        if not cleaned:
            return None
        canonical = [v.casefold() for v in cleaned]
        counts: dict[str, int] = {}
        for v in canonical:
            counts[v] = counts.get(v, 0) + 1
        best, freq = max(counts.items(), key=lambda kv: kv[1])
        if len(cleaned) >= 2 and (freq / len(cleaned)) < 0.7:
            return None
        for original in cleaned:
            if original.casefold() == best:
                return original
        return cleaned[0]

    artist_hint = _consensus((tag_hints or {}).get("artist"))
    album_hint = _consensus((tag_hints or {}).get("album"))

    diagnostics: dict[str, ReleasePromptDiagnostics] = {}
    total_files = len(pending_results)
    for key, _score in candidates:
        entries = daemon._release_track_entries(key, release_examples, discogs_details)
        if not entries:
            continue
        example = release_examples.get(key)

        matched = 0
        strong = 0
        total_best = 0.0
        for pending in pending_results:
            title = pending.meta.title
            duration = pending.meta.duration_seconds
            best = 0.0
            for track_title, track_duration in entries:
                score = (
                    combine_similarity(
                        title_similarity(title, track_title),
                        duration_similarity(duration, track_duration),
                    )
                    or 0.0
                )
                if score > best:
                    best = score
            if best >= cfg.match_threshold:
                matched += 1
                total_best += best
                if best >= cfg.strong_threshold:
                    strong += 1

        cov = matched / total_files if total_files else None
        avg = (total_best / matched) if matched else None
        consensus = (strong / matched) if matched else None

        reasons: list[str] = []
        if dir_track_count and example and example.track_total:
            total = int(example.track_total)
            if total and abs(total - dir_track_count) >= 2:
                reasons.append(f"tracks {dir_track_count}/{total}")
        if dir_year and example:
            year = daemon._parse_year(example.date) if example.date else None
            if year and abs(int(year) - int(dir_year)) >= 3:
                reasons.append(f"year {dir_year}/{year}")
        if artist_hint and example and example.artist:
            if daemon._token_overlap_ratio(artist_hint, example.artist) < cfg.overlap_mismatch_threshold:
                reasons.append("artist mismatch")
        if album_hint and example and example.title:
            if daemon._token_overlap_ratio(album_hint, example.title) < cfg.overlap_mismatch_threshold:
                reasons.append("album mismatch")
        if isinstance(cov, float) and cov < 0.5:
            reasons.append("low coverage")

        diagnostics[key] = ReleasePromptDiagnostics(
            coverage=cov,
            avg_confidence=avg,
            consensus=consensus,
            matched=matched,
            total=total_files,
            reasons=reasons,
        )

    return diagnostics
