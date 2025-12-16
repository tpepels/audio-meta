from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .config import ClassicalSettings
from .models import TrackMetadata

TITLE_PATTERN = re.compile(r"(symphony|concerto|suite|sonata|opus|op\.)", re.IGNORECASE)


@dataclass(slots=True)
class ClassicalDecision:
    is_classical: bool
    score: float


class ClassicalHeuristics:
    def __init__(self, settings: ClassicalSettings) -> None:
        self.settings = settings

    def evaluate(self, meta: TrackMetadata) -> ClassicalDecision:
        score = 0.0
        if meta.genre and self._match_keyword(meta.genre, self.settings.genre_keywords):
            score += 0.4
        if meta.title and TITLE_PATTERN.search(meta.title):
            score += 0.3
        if meta.duration_seconds and meta.duration_seconds >= self.settings.min_duration_seconds:
            score += 0.2
        if meta.composer and meta.artist and meta.composer != meta.artist:
            score += 0.1
        return ClassicalDecision(is_classical=score >= 0.5, score=round(score, 2))

    def adapt_metadata(self, meta: TrackMetadata) -> bool:
        decision = self.evaluate(meta)
        if not decision.is_classical:
            return False
        if not meta.composer:
            fallback = meta.album_artist or meta.artist
            if fallback:
                meta.composer = fallback
        if meta.composer:
            original_artist = meta.artist
            meta.album_artist = meta.composer
            performer_names = []
            if meta.conductor:
                performer_names.append(meta.conductor)
            if meta.performers:
                performer_names.extend(meta.performers)
            if not performer_names:
                performer_names.append(original_artist or meta.composer)
            meta.artist = "; ".join(performer_names)
        if meta.work and meta.title and not self._work_already_in_title(meta.work, meta.title):
            meta.title = f"{meta.work}: {meta.title}"
        if meta.performers:
            meta.extra["PERFORMERS"] = "; ".join(meta.performers)
        if meta.conductor:
            meta.extra["CONDUCTOR"] = meta.conductor
        return True

    @staticmethod
    def _normalize_for_compare(value: str) -> str:
        simplified = re.sub(r"[^\w]+", " ", value.lower())
        return re.sub(r"\s+", " ", simplified).strip()

    def _work_already_in_title(self, work: str, title: str) -> bool:
        work_norm = self._normalize_for_compare(work)
        title_norm = self._normalize_for_compare(title)
        if not work_norm or not title_norm:
            return False
        return work_norm in title_norm

    @staticmethod
    def _match_keyword(value: str, keywords: Iterable[str]) -> bool:
        lower = value.lower()
        return any(keyword.lower() in lower for keyword in keywords)
