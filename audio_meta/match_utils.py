from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional


def normalize_match_text(value: str) -> str:
    cleaned = unicodedata.normalize("NFKD", value)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return cleaned.strip()


def title_similarity(a: Optional[str], b: Optional[str]) -> Optional[float]:
    if not a or not b:
        return None
    norm_a = normalize_match_text(a)
    norm_b = normalize_match_text(b)
    if not norm_a or not norm_b:
        return None
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def duration_similarity(a: Optional[int], b: Optional[int]) -> Optional[float]:
    if not a or not b:
        return None
    diff = abs(a - b)
    if diff > max(20, int(0.25 * max(a, b))):
        return max(0.0, 1 - diff / (max(a, b) or 1))
    return max(0.0, 1 - diff / (max(a, b) or 1))


def combine_similarity(title_ratio: Optional[float], duration_ratio: Optional[float]) -> Optional[float]:
    score = 0.0
    weight = 0.0
    if title_ratio is not None:
        score += title_ratio * 0.7
        weight += 0.7
    if duration_ratio is not None:
        score += duration_ratio * 0.3
        weight += 0.3
    if weight == 0.0:
        return None
    return score / weight


def parse_discogs_duration(value: Optional[str]) -> Optional[int]:
    if not value or ":" not in value:
        return None
    parts = value.split(":", 1)
    try:
        minutes = int(parts[0])
        seconds_str = parts[1]
        if seconds_str.isdigit():
            seconds = int(seconds_str)
        else:
            seconds = int(float(seconds_str))
        return minutes * 60 + seconds
    except (ValueError, TypeError):
        return None

