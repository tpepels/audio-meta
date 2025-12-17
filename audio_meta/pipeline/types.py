from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnmatchedDecision:
    should_abort: bool


@dataclass(frozen=True)
class ReleaseFinalizeOutcome:
    provider: str | None
    release_id: str | None
    album_name: str
    album_artist: str
    discogs_release_details: dict | None
    release_summary_printed: bool


@dataclass(frozen=True)
class TrackSkipDecision:
    should_skip: bool
    reason: str | None = None
