from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from ..heuristics import guess_metadata_from_path
from ..models import TrackMetadata


def build_prompt_track_preview_lines(
    directory: Path,
    files: Optional[list[Path]],
    *,
    include_extensions: Sequence[str],
    limit: int,
    read_existing_tags: Callable[[TrackMetadata], Mapping[str, object]],
    apply_tag_hints: Callable[[TrackMetadata, Mapping[str, object]], None],
) -> list[str]:
    if limit <= 0:
        return []

    preview_files = select_prompt_preview_files(
        directory, files, include_extensions=include_extensions
    )[:limit]
    if not preview_files:
        return []

    lines: list[str] = []
    for file_path in preview_files:
        meta = TrackMetadata(path=file_path)
        tags = dict(read_existing_tags(meta) or {})
        apply_tag_hints(meta, tags)
        guess = guess_metadata_from_path(file_path)
        title = meta.title or guess.title or file_path.stem
        track_number = meta.track_number or guess.track_number
        disc_number = meta.disc_number
        disc_prefix = f"D{disc_number} " if isinstance(disc_number, int) else ""
        track_prefix = f"{track_number:02d}" if isinstance(track_number, int) else "??"

        details: list[str] = []
        if meta.artist:
            details.append(f"artist={meta.artist}")
        if meta.album_artist and meta.album_artist != meta.artist:
            details.append(f"album_artist={meta.album_artist}")
        if meta.album:
            details.append(f"album={meta.album}")
        if meta.composer:
            details.append(f"composer={meta.composer}")
        if meta.performers:
            performers = ", ".join(meta.performers[:2])
            if len(meta.performers) > 2:
                performers += ", …"
            details.append(f"performers={performers}")
        date_tag = tags.get("date") or tags.get("year")
        if isinstance(date_tag, str) and date_tag.strip():
            details.append(f"date={date_tag.strip()}")

        detail_suffix = f"  ({' | '.join(details)})" if details else ""
        lines.append(
            f"{disc_prefix}{track_prefix} · {title}{detail_suffix}  [{file_path.name}]"
        )
    return lines


def select_prompt_preview_files(
    directory: Path,
    files: Optional[list[Path]],
    *,
    include_extensions: Sequence[str],
) -> list[Path]:
    if files:
        candidates = [p for p in files if p.exists()]
        return sorted(candidates, key=lambda p: p.name.lower())

    if not directory.exists():
        return []

    exts = {ext.lower() for ext in include_extensions}
    try:
        candidates = [
            p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts
        ]
    except OSError:
        return []
    return sorted(candidates, key=lambda p: p.name.lower())
