from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

from .models import TrackMetadata
from .providers.musicbrainz import LookupResult


class DryRunRecorder:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self._lock = Lock()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")

    def record(
        self,
        meta: TrackMetadata,
        score: Optional[float],
        tag_changes: Optional[dict] = None,
        relocate_from: Optional[Path] = None,
        relocate_to: Optional[Path] = None,
    ) -> None:
        payload = meta.to_record()
        payload["match_score"] = score
        if tag_changes:
            payload["tag_changes"] = tag_changes
        if relocate_to:
            payload["relocate_from"] = str(relocate_from or meta.path)
            payload["relocate_to"] = str(relocate_to)
        line = json.dumps(payload, indent=2, sort_keys=True)
        with self._lock:
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


@dataclass
class PlannedUpdate:
    meta: TrackMetadata
    score: Optional[float]
    tag_changes: dict
    target_path: Optional[Path]


@dataclass
class ReleaseExample:
    provider: str
    title: str
    artist: str
    date: Optional[str]
    track_total: Optional[int]
    disc_count: Optional[int]
    formats: list[str]


@dataclass
class PendingResult:
    meta: TrackMetadata
    result: Optional[LookupResult]
    matched: bool
    existing_tags: dict[str, Optional[str]] = field(default_factory=dict)

