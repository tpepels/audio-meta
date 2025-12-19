from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..classical import ClassicalHeuristics
from ..directory_identity import normalize_hint_value
from ..models import TrackMetadata

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings


class ClassicalMusicService:
    """Service for handling classical music metadata validation and analysis."""

    def __init__(
        self,
        heuristics: ClassicalHeuristics,
        settings: Settings,
    ) -> None:
        self.heuristics = heuristics
        self.settings = settings

    def should_review_credits(self, metas: list[TrackMetadata]) -> bool:
        """
        Determine if classical performer credits need review.

        Returns True if:
        - Coverage is below threshold
        - Consensus is below threshold
        - Insufficient hinted tracks
        """
        min_tracks = int(
            getattr(self.settings.daemon, "classical_credits_min_tracks", 3) or 3
        )
        min_coverage = float(
            getattr(self.settings.daemon, "classical_credits_min_coverage", 0.6) or 0.6
        )
        min_consensus = float(
            getattr(self.settings.daemon, "classical_credits_min_consensus", 0.7)
            or 0.7
        )

        stats = self.calculate_credits_stats(metas)
        if stats["classical_tracks"] < min_tracks:
            return False
        if stats["coverage"] < min_coverage:
            return True
        if stats["hinted_tracks"] < min_tracks:
            return True
        if stats["consensus"] is None:
            return True
        return stats["consensus"] < min_consensus

    def calculate_credits_stats(
        self, metas: list[TrackMetadata]
    ) -> dict[str, object]:
        """
        Calculate statistics about classical performer credits.

        Returns a dict with:
        - classical_tracks: Number of classical tracks detected
        - hinted_tracks: Number of tracks with performer hints
        - missing_hints: Number of tracks without hints
        - coverage: Ratio of hinted to classical tracks
        - consensus: Consensus ratio for most common performer hint
        - top_hints: Top 5 performer hints with counts
        """
        classical = [m for m in metas if self.heuristics.evaluate(m).is_classical]
        hints: list[str] = []
        missing = 0

        for meta in classical:
            parts: list[str] = []

            # Collect performer credits
            if meta.performers:
                parts.extend(meta.performers)

            # Add album artist if different from composer
            if meta.album_artist and meta.album_artist != meta.composer:
                parts.append(meta.album_artist)
            elif meta.artist and meta.artist != meta.composer:
                parts.append(meta.artist)

            # Add conductor if different from composer
            if meta.conductor and meta.conductor != meta.composer:
                parts.append(meta.conductor)

            # Extract unique tokens
            unique: list[str] = []
            for value in parts:
                for token in self.heuristics._split_artist_tokens(value):
                    if token and token not in unique and token != meta.composer:
                        unique.append(token)

            if not unique:
                missing += 1
                continue

            hints.append("; ".join(unique))

        # Calculate coverage
        coverage = (len(hints) / len(classical)) if classical else 1.0

        # Calculate consensus
        counts: dict[str, int] = {}
        canonical_to_display: dict[str, str] = {}
        for hint in hints:
            canonical = normalize_hint_value(hint)
            if not canonical:
                continue
            counts[canonical] = counts.get(canonical, 0) + 1
            canonical_to_display.setdefault(canonical, hint)

        top = sorted(
            ((canonical_to_display[k], v) for k, v in counts.items()),
            key=lambda kv: kv[1],
            reverse=True,
        )

        consensus = None
        if hints and counts:
            best = max(counts.values())
            consensus = best / len(hints)

        return {
            "classical_tracks": len(classical),
            "hinted_tracks": len(hints),
            "missing_hints": missing,
            "coverage": float(coverage),
            "consensus": consensus,
            "top_hints": top[:5],
        }

    def extract_performer_hints(self, metas: list[TrackMetadata]) -> list[str]:
        """
        Extract performer hints from classical tracks.

        Returns a list of formatted performer hints (semicolon-separated).
        """
        classical = [m for m in metas if self.heuristics.evaluate(m).is_classical]
        hints: list[str] = []

        for meta in classical:
            parts: list[str] = []

            if meta.performers:
                parts.extend(meta.performers)

            if meta.album_artist and meta.album_artist != meta.composer:
                parts.append(meta.album_artist)
            elif meta.artist and meta.artist != meta.composer:
                parts.append(meta.artist)

            if meta.conductor and meta.conductor != meta.composer:
                parts.append(meta.conductor)

            unique: list[str] = []
            for value in parts:
                for token in self.heuristics._split_artist_tokens(value):
                    if token and token not in unique and token != meta.composer:
                        unique.append(token)

            if unique:
                hints.append("; ".join(unique))

        return hints

    def is_classical_track(self, meta: TrackMetadata) -> bool:
        """Check if a track is classified as classical music."""
        return self.heuristics.evaluate(meta).is_classical

    def get_credits_action(self) -> str:
        """Get the configured action for classical credits review."""
        return (
            getattr(self.settings.daemon, "classical_credits_action", "defer")
            or "defer"
        )
