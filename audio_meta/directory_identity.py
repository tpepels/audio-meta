from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional


def looks_like_disc_folder(name: str) -> bool:
    return bool(re.search(r"(?:^|\s)(disc|cd|disk)\s*\d", name, re.IGNORECASE))


def path_based_hints(directory: Path) -> tuple[Optional[str], Optional[str]]:
    names: list[str] = []
    current = directory
    for _ in range(3):
        if not current or not current.name:
            break
        names.append(current.name)
        if current.parent == current:
            break
        current = current.parent
    album = None
    album_index = None
    for idx, name in enumerate(names):
        if name and not looks_like_disc_folder(name):
            album = name
            album_index = idx
            break
    if album is None:
        album = names[0] if names else None
    artist = None
    if album_index is not None:
        for name in names[album_index + 1 :]:
            if name and not looks_like_disc_folder(name):
                artist = name
                break
    return artist, album


def hint_cache_key(artist: Optional[str], album: Optional[str]) -> Optional[str]:
    normalized_album = normalize_hint_value(album)
    if not normalized_album:
        return None
    normalized_artist = normalize_hint_value(artist) or "unknown"
    return f"hint://{normalized_artist}|{normalized_album}"


def normalize_hint_value(value: Optional[str]) -> str:
    if not value:
        return ""
    cleaned = unicodedata.normalize("NFKD", value)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return cleaned.strip()


def token_overlap_ratio(expected: Optional[str], candidate: Optional[str]) -> float:
    expected_tokens = tokenize(expected)
    if not expected_tokens:
        return 0.0
    candidate_tokens = set(tokenize(candidate))
    if not candidate_tokens:
        return 0.0
    overlap = sum(1 for token in expected_tokens if token in candidate_tokens)
    return overlap / len(expected_tokens)


def tokenize(value: Optional[str]) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        value = " ".join(str(part) for part in value if part)
    else:
        value = str(value)
    cleaned = unicodedata.normalize("NFKD", value)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return [token for token in cleaned.split() if token]
