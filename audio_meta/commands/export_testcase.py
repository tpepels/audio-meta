from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Optional, cast

from ..daemon import AudioMetaDaemon
from ..daemon_types import PendingResult, ReleaseExample
from ..models import TrackMetadata
from ..providers.musicbrainz import ReleaseData
from ..providers.musicbrainz import LookupResult
from ..pipeline.contexts import (
    DirectoryContext,
    TrackEnrichmentContext,
    TrackSignalContext,
    TrackSkipContext,
)
from ..scanner import DirectoryBatch


@dataclass(slots=True)
class ExportedPending:
    path: str
    matched: bool
    existing_tags: dict[str, Optional[str]]
    meta: dict[str, Any]


@dataclass(slots=True)
class ExportedCase:
    schema: str
    generated_at: str
    audio_meta_version: str
    directory: str
    display_directory: str
    file_count: int
    is_singleton: bool
    dir_track_count: int
    dir_year: Optional[int]
    pending_results: list[ExportedPending]
    release_scores: dict[str, float]
    release_examples: dict[str, ReleaseExample]
    discogs_details: dict[str, dict]
    musicbrainz_releases: dict[str, dict]
    expected: dict[str, Any]


def _release_data_to_dict(data: ReleaseData) -> dict[str, Any]:
    return {
        "release_id": data.release_id,
        "album_title": data.album_title,
        "album_artist": data.album_artist,
        "release_date": data.release_date,
        "disc_count": data.disc_count,
        "formats": list(data.formats),
        "tracks": [
            {
                "recording_id": t.recording_id,
                "disc_number": t.disc_number,
                "number": t.number,
                "title": t.title,
                "duration_seconds": t.duration_seconds,
            }
            for t in list(data.tracks or [])
        ],
    }


def _release_example_to_dict(value: ReleaseExample) -> dict[str, Any]:
    return asdict(value)


def run(
    daemon: AudioMetaDaemon,
    *,
    directory: Path,
    out: Path,
    expected_release_key: Optional[str] = None,
) -> None:
    batch = daemon.scanner.collect_directory(directory)
    if not batch:
        raise SystemExit(f"No audio files found in {directory}")

    prepared = daemon._prepare_album_batch(
        DirectoryBatch(directory=batch.directory, files=list(batch.files)),
        force_prompt=True,
    )
    if not prepared:
        raise SystemExit(f"Could not prepare album batch for {directory}")

    is_singleton = daemon._is_singleton_directory(prepared)
    directory_hash = daemon._calculate_directory_hash(
        prepared.directory, prepared.files
    )
    dir_ctx = DirectoryContext(
        daemon=daemon,
        directory=prepared.directory,
        files=list(prepared.files),
        force_prompt=True,
        is_singleton=is_singleton,
        directory_hash=directory_hash,
    )
    if daemon.pipeline.should_skip_directory(dir_ctx):
        raise SystemExit(f"Directory would be skipped by pipeline: {directory}")

    pending_results: list[PendingResult] = []
    dir_ctx.pending_results = pending_results
    dir_ctx.release_scores = {}
    dir_ctx.release_examples = {}
    dir_ctx.discogs_details = {}

    daemon.pipeline.analyze_directory(dir_ctx)
    daemon.pipeline.initialize_directory(dir_ctx)

    for file_path in prepared.files:
        meta = TrackMetadata(path=file_path)
        if dir_ctx.dir_track_count:
            meta.track_total = int(dir_ctx.dir_track_count)
        existing_tags = daemon._read_existing_tags(meta)
        daemon._apply_tag_hints(meta, existing_tags)
        daemon.pipeline.extract_signals(
            TrackSignalContext(
                daemon=daemon,
                directory=prepared.directory,
                meta=meta,
                existing_tags=dict(existing_tags),
            )
        )
        if daemon.pipeline.should_skip_track(
            TrackSkipContext(
                daemon=daemon,
                directory=prepared.directory,
                file_path=file_path,
                directory_ctx=dir_ctx,
            )
        ):
            continue
        result = cast(
            Optional[LookupResult],
            daemon.pipeline.enrich_track(
                TrackEnrichmentContext(
                    daemon=daemon,
                    directory=prepared.directory,
                    meta=meta,
                    existing_tags=dict(existing_tags),
                )
            ),
        )
        pending_results.append(
            PendingResult(
                meta=meta,
                result=result,
                matched=bool(result),
                existing_tags=dict(existing_tags),
            )
        )

    if not dir_ctx.release_scores and dir_ctx.discogs_release_details:
        daemon._apply_discogs_release_details(
            pending_results, dir_ctx.discogs_release_details
        )

    daemon.pipeline.add_candidates(dir_ctx)

    if expected_release_key:
        if ":" in expected_release_key:
            provider, rid = expected_release_key.split(":", 1)
        else:
            provider, rid = "musicbrainz", expected_release_key
        dir_ctx.forced_provider = provider
        dir_ctx.forced_release_id = rid
        dir_ctx.forced_release_score = 1.0

    decision = daemon.pipeline.decide_release(dir_ctx)

    mb_releases: dict[str, dict] = {}
    for key in list(dir_ctx.release_examples.keys()):
        if not key.startswith("musicbrainz:"):
            continue
        _, release_id = key.split(":", 1)
        release = (
            daemon.musicbrainz.release_tracker.releases.get(release_id)
            if daemon.musicbrainz
            else None
        )
        if not release and daemon.musicbrainz:
            release = daemon.musicbrainz._fetch_release_tracks(release_id)
            if release:
                daemon.musicbrainz.release_tracker.releases[release_id] = release
        if release:
            mb_releases[release_id] = _release_data_to_dict(release)

    case = ExportedCase(
        schema="audio-meta.release-selection-case.v1",
        generated_at=datetime.now(timezone.utc).isoformat(),
        audio_meta_version=metadata.version("audio-meta"),
        directory=str(prepared.directory),
        display_directory=daemon._display_path(prepared.directory),
        file_count=len(prepared.files),
        is_singleton=bool(is_singleton),
        dir_track_count=int(dir_ctx.dir_track_count or 0),
        dir_year=dir_ctx.dir_year,
        pending_results=[
            ExportedPending(
                path=daemon._display_path(p.meta.path),
                matched=bool(p.matched),
                existing_tags=dict(p.existing_tags),
                meta={
                    "title": p.meta.title,
                    "album": p.meta.album,
                    "artist": p.meta.artist,
                    "album_artist": p.meta.album_artist,
                    "composer": p.meta.composer,
                    "work": p.meta.work,
                    "movement": p.meta.movement,
                    "genre": p.meta.genre,
                    "duration_seconds": p.meta.duration_seconds,
                    "musicbrainz_release_id": p.meta.musicbrainz_release_id,
                    "musicbrainz_track_id": p.meta.musicbrainz_track_id,
                    "match_confidence": p.meta.match_confidence,
                    "extra": dict(p.meta.extra),
                },
            )
            for p in pending_results
        ],
        release_scores=dict(dir_ctx.release_scores),
        release_examples=dict(dir_ctx.release_examples),
        discogs_details=dict(dir_ctx.discogs_details),
        musicbrainz_releases=mb_releases,
        expected={
            "forced_release_key": expected_release_key,
            "best_release_id": decision.best_release_id,
            "best_score": decision.best_score,
            "ambiguous_candidates": list(decision.ambiguous_candidates),
            "coverage": decision.coverage,
            "should_abort": decision.should_abort,
        },
    )

    payload = asdict(case)
    payload["release_examples"] = {
        k: _release_example_to_dict(v) for k, v in case.release_examples.items()
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote testcase to {out}")
