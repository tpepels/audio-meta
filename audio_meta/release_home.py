from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Any

from .models import TrackMetadata

logger = logging.getLogger(__name__)


def maybe_set_release_home(
    daemon: Any,
    release_key: str,
    directory: Path,
    *,
    track_count: int,
    directory_hash: Optional[str],
) -> None:
    existing = daemon.cache.get_release_home(release_key)
    if existing:
        existing_dir_str, existing_count, existing_hash = existing
        existing_dir = Path(existing_dir_str)
        if not existing_dir.exists():
            daemon.cache.delete_release_home(release_key)
        else:
            current_hash = daemon.cache.get_directory_hash(existing_dir)
            if existing_hash and current_hash and existing_hash != current_hash:
                daemon.cache.delete_release_home(release_key)
            else:
                effective_existing = int(
                    existing_count or daemon._count_audio_files(existing_dir)
                )
                if existing_dir != directory and effective_existing >= track_count:
                    daemon.cache.set_release_home(
                        release_key,
                        existing_dir,
                        track_count=effective_existing,
                        directory_hash=current_hash or existing_hash,
                    )
                    return
    daemon.cache.set_release_home(
        release_key, directory, track_count=track_count, directory_hash=directory_hash
    )


def select_singleton_release_home(
    daemon: Any,
    release_key: str,
    current_dir: Path,
    current_count: int,
    best_release_score: float,
    sample_meta: Optional[TrackMetadata],
) -> Optional[Path]:
    min_home_tracks = 3
    min_release_score = 0.65
    min_track_match = 0.85
    provider, release_id = daemon._split_release_key(release_key)
    if best_release_score < min_release_score:
        return None
    if provider != "musicbrainz" or not release_id:
        return None
    candidate_homes: set[Path] = set()
    cached = daemon.cache.get_release_home(release_key)
    if cached:
        raw_path, _, cached_hash = cached
        cached_path = Path(raw_path)
        if cached_path.exists() and cached_path != current_dir:
            current_hash = daemon.cache.get_directory_hash(cached_path)
            if cached_hash and current_hash and cached_hash != current_hash:
                daemon.cache.delete_release_home(release_key)
            else:
                candidate_homes.add(cached_path)
        elif not cached_path.exists():
            daemon.cache.delete_release_home(release_key)

    fallback = daemon._find_release_home(release_id, current_dir, current_count)
    if fallback and fallback != current_dir:
        candidate_homes.add(fallback)
    for raw in daemon.cache.find_directories_for_release(release_id):
        candidate = Path(raw)
        if candidate == current_dir or not candidate.exists():
            continue
        candidate_homes.add(daemon._album_root(candidate))

    filtered: list[Path] = []
    for home in sorted(candidate_homes, key=lambda p: str(p)):
        count = daemon._count_audio_files(home)
        if count >= min_home_tracks:
            filtered.append(home)
    if not filtered:
        return None
    if len(filtered) > 1:
        if daemon.defer_prompts and not daemon._processing_deferred:
            daemon._schedule_deferred_directory(current_dir, "release_home_conflict")
        return None

    release_data = daemon.musicbrainz.release_tracker.releases.get(release_id)
    if not release_data:
        release_data = daemon.musicbrainz._fetch_release_tracks(release_id)
        if release_data:
            daemon.musicbrainz.release_tracker.releases[release_id] = release_data
    if not release_data or not release_data.tracks or not sample_meta:
        return None
    track_match = daemon._match_pending_to_release(sample_meta, release_data)
    if not track_match or track_match < min_track_match:
        return None
    return filtered[0]


def plan_singleton_target(
    daemon: Any,
    meta: TrackMetadata,
    release_home_dir: Path,
    is_classical: bool,
) -> Optional[Path]:
    canonical = daemon.organizer.canonical_target(meta, is_classical)
    filename = Path(canonical).name if canonical else meta.path.name
    target = release_home_dir / filename
    try:
        return daemon.organizer._truncate_target(target)
    except AttributeError:  # pragma: no cover - organizer API safeguard
        return target
