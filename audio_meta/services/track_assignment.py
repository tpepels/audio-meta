from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..assignment import best_assignment_max_score
from ..daemon_types import PendingResult
from ..heuristics import guess_metadata_from_path
from ..match_utils import (
    duration_similarity,
    normalize_title_for_match,
    parse_discogs_duration,
    title_similarity,
)
from ..models import TrackMetadata
from ..providers.musicbrainz import LookupResult, ReleaseMatch

if TYPE_CHECKING:  # pragma: no cover
    from ..providers.discogs import DiscogsClient
    from ..providers.musicbrainz import MusicBrainzClient, ReleaseData

logger = logging.getLogger(__name__)


class TrackAssignmentService:
    """Service for assigning tracks to releases using Hungarian algorithm."""

    def __init__(
        self,
        musicbrainz: MusicBrainzClient,
        discogs: Optional[DiscogsClient],
    ) -> None:
        self.musicbrainz = musicbrainz
        self.discogs = discogs

    def assign_musicbrainz_tracks(
        self,
        directory: Path,
        release_id: str,
        pending_results: list[PendingResult],
        force: bool = False,
    ) -> tuple[bool, int, float]:
        """
        Assign tracks to a MusicBrainz release using Hungarian algorithm.

        Returns:
            Tuple of (applied, assigned_count, avg_confidence)
        """
        # Register and fetch release
        self.musicbrainz.release_tracker.register(
            directory,
            release_id,
            self.musicbrainz._fetch_release_tracks,
        )
        self.musicbrainz.release_tracker.remember_release(directory, release_id, 1.0)

        release_data = self.musicbrainz.release_tracker.releases.get(release_id)
        if not release_data:
            return False, 0, 0.0

        if force:
            release_data.claimed.clear()

        applied = False
        to_assign: list[PendingResult] = []

        # Collect tracks that need assignment
        for pending in pending_results:
            if pending.matched and not force:
                applied = True
                continue
            if force:
                pending.matched = False

            # Ensure duration is available
            if not pending.meta.duration_seconds:
                duration = self.musicbrainz._probe_duration(pending.meta.path)
                if duration:
                    pending.meta.duration_seconds = duration

            to_assign.append(pending)

        if to_assign:
            assigned_count, assigned_total_score = self._perform_musicbrainz_assignment(
                to_assign, release_data
            )
            applied = applied or (assigned_count > 0)
            avg = (assigned_total_score / assigned_count) if assigned_count else 0.0
            return applied, assigned_count, avg

        return applied, 0, 0.0

    def assign_discogs_tracks(
        self,
        pending_results: list[PendingResult],
        release_details: dict,
    ) -> bool:
        """
        Assign tracks to a Discogs release using Hungarian algorithm.

        Returns:
            True if any assignments were made
        """
        if not self.discogs:
            return False

        tracklist = release_details.get("tracklist") or []
        tracks = [
            t for t in tracklist if t.get("type_", "track") in (None, "", "track")
        ]

        # If no tracks, apply release details to all
        if not tracks:
            for pending in pending_results:
                self.discogs.apply_release_details(
                    pending.meta, release_details, allow_overwrite=True
                )
                score = pending.meta.match_confidence or 0.4
                pending.meta.match_confidence = score
                pending.result = LookupResult(pending.meta, score=score)
                pending.matched = True
            return True

        # Build assignment score matrix
        assignment_scores = self._build_discogs_score_matrix(pending_results, tracks)

        # Perform assignment
        assignment = best_assignment_max_score(assignment_scores, dummy_score=0.55)

        # Apply assignments
        applied = False
        for pending_index, track_index in enumerate(assignment):
            pending = pending_results[pending_index]
            if track_index is None or track_index >= len(tracks):
                continue

            score = assignment_scores[pending_index][track_index]
            if score < 0.58:
                continue

            track = tracks[track_index]
            self.discogs.apply_release_details_matched(
                pending.meta, release_details, track, allow_overwrite=True
            )

            # Set track number if available
            position = track.get("position")
            if pending.meta.track_number is None and isinstance(position, str):
                pos_num = self.discogs._parse_track_number(position)
                if isinstance(pos_num, int):
                    pending.meta.track_number = pos_num

            # Update confidence
            pending.meta.match_confidence = max(
                pending.meta.match_confidence or 0.0, 0.35 + score * 0.3
            )
            pending.result = LookupResult(
                pending.meta,
                score=max(
                    pending.result.score if pending.result else 0.0, 0.35 + score * 0.3
                ),
            )
            pending.matched = True
            applied = True

        return applied

    def _perform_musicbrainz_assignment(
        self,
        to_assign: list[PendingResult],
        release_data: ReleaseData,
    ) -> tuple[int, float]:
        """
        Perform the actual MusicBrainz track assignment.

        Returns:
            Tuple of (assigned_count, total_score)
        """
        assignment_scores = self._build_musicbrainz_score_matrix(
            to_assign, release_data
        )

        assignment = best_assignment_max_score(assignment_scores, dummy_score=0.62)

        assigned_count = 0
        assigned_total_score = 0.0
        tracks = list(release_data.tracks or [])

        for pending_index, track_index in enumerate(assignment):
            if track_index is None or track_index >= len(tracks):
                continue

            score = assignment_scores[pending_index][track_index]
            if score < 0.63:
                continue

            track = tracks[track_index]
            release_match = ReleaseMatch(
                release=release_data, track=track, confidence=score
            )

            lookup = self.musicbrainz.apply_release_match(
                to_assign[pending_index].meta, release_match
            )

            if lookup:
                to_assign[pending_index].result = lookup
                to_assign[pending_index].matched = True
                assigned_count += 1
                assigned_total_score += float(score)
                release_data.mark_claimed(track.recording_id)

                # Set track/disc numbers
                if (
                    to_assign[pending_index].meta.track_number is None
                    and isinstance(track.number, int)
                ):
                    to_assign[pending_index].meta.track_number = track.number

                if (
                    to_assign[pending_index].meta.disc_number is None
                    and isinstance(track.disc_number, int)
                ):
                    to_assign[pending_index].meta.disc_number = track.disc_number

        return assigned_count, assigned_total_score

    def _build_musicbrainz_score_matrix(
        self,
        pending_results: list[PendingResult],
        release_data: ReleaseData,
    ) -> list[list[float]]:
        """
        Build score matrix for MusicBrainz track assignment.

        Each row represents a pending track, each column a release track.
        """
        assignment_scores: list[list[float]] = []
        tracks = list(release_data.tracks or [])

        for pending in pending_results:
            meta = pending.meta
            guess = guess_metadata_from_path(meta.path)
            track_number = meta.track_number or guess.track_number
            disc_number = meta.disc_number
            title = meta.title or guess.title or meta.path.stem
            title_norm = normalize_title_for_match(title)

            row: list[float] = []
            for track in tracks:
                # Check for exact recording ID match
                if (
                    meta.musicbrainz_track_id
                    and track.recording_id
                    and meta.musicbrainz_track_id == track.recording_id
                ):
                    row.append(1.0)
                    continue

                score = 0.0

                # Track number matching (62% weight)
                if isinstance(track_number, int) and track.number:
                    diff = abs(track.number - track_number)
                    if diff == 0:
                        score += 0.62
                    elif diff == 1:
                        score += 0.28
                    elif diff == 2:
                        score += 0.12

                # Disc number matching (8% bonus or -4% penalty)
                if isinstance(disc_number, int) and track.disc_number:
                    if disc_number == track.disc_number:
                        score += 0.08
                    else:
                        score -= 0.04

                # Title similarity (25% weight, +45% bonus for exact match)
                if title_norm and track.title:
                    ratio = title_similarity(title_norm, track.title) or 0.0
                    score += 0.25 * ratio
                    if ratio >= 0.98:
                        score += 0.45
                elif title and track.title:
                    ratio = title_similarity(title, track.title) or 0.0
                    score += 0.2 * ratio

                # Duration similarity (5% weight)
                dur_ratio = duration_similarity(
                    meta.duration_seconds, track.duration_seconds
                )
                if dur_ratio is not None:
                    score += 0.05 * dur_ratio

                row.append(max(0.0, min(1.0, score)))

            assignment_scores.append(row)

        return assignment_scores

    def _build_discogs_score_matrix(
        self,
        pending_results: list[PendingResult],
        tracks: list[dict],
    ) -> list[list[float]]:
        """
        Build score matrix for Discogs track assignment.

        Each row represents a pending track, each column a release track.
        """
        assignment_scores: list[list[float]] = []

        for pending in pending_results:
            meta = pending.meta

            # Ensure duration is available
            if not meta.duration_seconds:
                duration = self.musicbrainz._probe_duration(meta.path)
                if duration:
                    meta.duration_seconds = duration

            guess = guess_metadata_from_path(meta.path)
            track_number = meta.track_number or guess.track_number
            title = meta.title or guess.title or meta.path.stem
            title_norm = normalize_title_for_match(title) or title

            row: list[float] = []
            for track in tracks:
                score = 0.0

                # Track number matching (60% weight)
                position = track.get("position")
                pos_num = (
                    self.discogs._parse_track_number(position)
                    if self.discogs and isinstance(position, str)
                    else None
                )

                if isinstance(track_number, int) and pos_num:
                    diff = abs(pos_num - track_number)
                    if diff == 0:
                        score += 0.6
                    elif diff == 1:
                        score += 0.25
                    elif diff == 2:
                        score += 0.1

                # Title similarity (30% weight)
                if track.get("title"):
                    ratio = title_similarity(title_norm, track.get("title")) or 0.0
                    score += 0.3 * ratio

                # Duration similarity (10% weight)
                dur = parse_discogs_duration(track.get("duration"))
                dur_ratio = duration_similarity(meta.duration_seconds, dur)
                if dur_ratio is not None:
                    score += 0.1 * dur_ratio

                row.append(max(0.0, min(1.0, score)))

            assignment_scores.append(row)

        return assignment_scores
