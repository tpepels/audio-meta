from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TRACK_PATTERN = re.compile(r"^(?P<num>\d{1,3})(?:[\s._-]+)(?P<title>.+)$")
ARTIST_ALBUM_PATTERN = re.compile(r"^(?P<artist>[^/]+?)\s*[-–]\s*(?P<album>.+)$")


@dataclass(slots=True)
class PathGuess:
    artist: Optional[str] = None
    album: Optional[str] = None
    title: Optional[str] = None
    track_number: Optional[int] = None

    def confidence(self) -> float:
        score = 0.0
        if self.artist:
            score += 0.25
        if self.album:
            score += 0.25
        if self.title:
            score += 0.25
        if self.track_number is not None:
            score += 0.25
        return score


def guess_metadata_from_path(path: Path) -> PathGuess:
    guess = PathGuess()
    filename = path.stem
    track_match = TRACK_PATTERN.match(filename)
    embedded_match = None
    if not track_match:
        embedded_match = _embedded_track_match(filename)
    if track_match:
        guess.track_number = int(track_match.group("num"))
        guess.title = _clean(track_match.group("title"))
    elif embedded_match:
        guess.track_number = embedded_match[0]
        guess.title = _clean(embedded_match[1])
    else:
        guess.title = _clean(filename)

    parent_parts = path.parts[:-1]
    if not parent_parts:
        return guess

    album_dir = parent_parts[-1]
    artist_dir = parent_parts[-2] if len(parent_parts) >= 2 else None
    match = ARTIST_ALBUM_PATTERN.match(album_dir)
    if match:
        guess.artist = _clean(match.group("artist"))
        guess.album = _clean(match.group("album"))
    else:
        guess.album = _clean(album_dir)
        if artist_dir:
            guess.artist = _clean(artist_dir)

    return guess


def _clean(value: str | None) -> Optional[str]:
    if not value:
        return None
    cleaned = value.replace("_", " ").strip(" ._-")
    return cleaned or None


def _embedded_track_match(filename: str) -> Optional[tuple[int, str]]:
    parts = filename.split(" - ")
    if len(parts) < 3:
        return None
    for idx, part in enumerate(parts[:-1]):
        num_part = part.strip()
        if num_part.isdigit():
            num = int(num_part)
            title = " - ".join(parts[idx + 1 :]).strip()
            if title:
                return num, title
    return None
