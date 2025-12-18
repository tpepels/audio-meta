import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from audio_meta.daemon_types import PendingResult, ReleaseExample
from audio_meta.directory_identity import path_based_hints, token_overlap_ratio
from audio_meta.match_utils import combine_similarity, duration_similarity, normalize_match_text, title_similarity
from audio_meta.models import TrackMetadata
from audio_meta.providers.musicbrainz import ReleaseData, ReleaseTrack
from audio_meta.release_scoring import adjust_release_scores
from audio_meta.release_selection import decide_release


FIXTURE_DIR = Path("tests/fixtures/release_selection")


@dataclass
class _ReleaseTracker:
    releases: dict[str, ReleaseData]


class _MusicBrainzStub:
    def __init__(self, releases: dict[str, ReleaseData]) -> None:
        self.release_tracker = _ReleaseTracker(releases=releases)

    def _fetch_release_tracks(self, release_id: str) -> Optional[ReleaseData]:
        return self.release_tracker.releases.get(release_id)


class _FixtureDaemon:
    interactive = False
    defer_prompts = False
    _processing_deferred = False
    discogs = None

    def __init__(self, mb_releases: dict[str, ReleaseData]) -> None:
        self.musicbrainz = _MusicBrainzStub(releases=mb_releases)

    @staticmethod
    def _release_key(provider: str, release_id: str) -> str:
        return f"{provider}:{release_id}"

    @staticmethod
    def _split_release_key(key: str) -> tuple[str, str]:
        if ":" in key:
            provider, rid = key.split(":", 1)
            return provider, rid
        return "musicbrainz", key

    @staticmethod
    def _parse_year(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        import re

        match = re.search(r"(19|20)\d{2}", value)
        return int(match.group(0)) if match else None

    def _token_overlap_ratio(self, expected: Optional[str], candidate: Optional[str]) -> float:
        return token_overlap_ratio(expected, candidate)

    def _path_based_hints(self, directory: Path) -> tuple[Optional[str], Optional[str]]:
        return path_based_hints(directory)

    def _match_pending_to_release(self, meta: TrackMetadata, release: ReleaseData) -> Optional[float]:
        title = meta.title
        duration = meta.duration_seconds
        best = 0.0
        for track in release.tracks:
            combined = combine_similarity(
                title_similarity(title, track.title),
                duration_similarity(duration, track.duration_seconds),
            )
            if combined is not None and combined > best:
                best = combined
        if best <= 0.0:
            return None
        return best

    def _adjust_release_scores(self, scores, release_examples, dir_track_count, dir_year, pending_results, directory, discogs_details):
        return adjust_release_scores(
            self,
            scores=scores,
            release_examples=release_examples,
            dir_track_count=dir_track_count,
            dir_year=dir_year,
            pending_results=pending_results,
            directory=directory,
            discogs_details=discogs_details,
        )

    def _release_track_entries(self, key: str, _release_examples: dict[str, ReleaseExample], discogs_details: dict[str, dict]):
        provider, release_id = self._split_release_key(key)
        if provider == "musicbrainz":
            release = self.musicbrainz.release_tracker.releases.get(release_id)
            if not release:
                return None
            entries: list[tuple[str, Optional[int]]] = []
            for track in release.tracks:
                if not track.title:
                    return None
                entries.append((normalize_match_text(track.title), track.duration_seconds))
            return entries or None
        if provider == "discogs":
            details = discogs_details.get(key)
            if not details:
                return None
            tracklist = details.get("tracklist") or []
            entries: list[tuple[str, Optional[int]]] = []
            for track in tracklist:
                if track.get("type_", "track") not in (None, "", "track"):
                    continue
                title = track.get("title")
                if not title:
                    return None
                entries.append((normalize_match_text(title), None))
            return entries or None
        return None

    def _canonical_release_signature(self, key: str, release_examples: dict[str, ReleaseExample], discogs_details: dict[str, dict]):
        entries = self._release_track_entries(key, release_examples, discogs_details)
        if not entries:
            return None
        normalized = []
        for title, duration in entries:
            if not title:
                return None
            normalized.append((title, duration))
        return len(normalized), tuple(normalized)

    def _auto_pick_equivalent_release(self, candidates, release_examples, discogs_details):
        signatures: dict[str, Any] = {}
        for key, _score in candidates:
            sig = self._canonical_release_signature(key, release_examples, discogs_details)
            if sig is None:
                return None
            signatures[key] = sig
        if not signatures:
            return None
        first = next(iter(signatures.values()))
        if not all(sig == first for sig in signatures.values()):
            return None
        priority = {"musicbrainz": 0, "discogs": 1}
        return min(
            signatures.keys(),
            key=lambda k: (priority.get(self._split_release_key(k)[0], 99), k),
        )

    def _auto_pick_existing_release_home(self, *_args, **_kwargs):
        return None

    def _release_home_for_key(self, *_args, **_kwargs):
        return None, 0

    def _warn_ambiguous_release(self, *_args, **_kwargs) -> None:
        return None

    def _record_skip(self, *_args, **_kwargs) -> None:
        return None

    def _schedule_deferred_directory(self, *_args, **_kwargs):
        raise AssertionError("defer_prompts is disabled in fixture tests")

    def _display_path(self, path: Path) -> str:
        return str(path)


def _load_release_data(payload: dict[str, Any]) -> ReleaseData:
    release = ReleaseData(
        payload["release_id"],
        payload.get("album_title"),
        payload.get("album_artist"),
        payload.get("release_date"),
    )
    release.disc_count = int(payload.get("disc_count") or 0)
    release.formats = list(payload.get("formats") or [])
    for t in payload.get("tracks") or []:
        release.add_track(
            ReleaseTrack(
                recording_id=t["recording_id"],
                disc_number=t.get("disc_number"),
                number=t.get("number"),
                title=t.get("title"),
                duration_seconds=t.get("duration_seconds"),
            )
        )
    return release


class TestExportedReleaseSelectionFixtures(unittest.TestCase):
    def test_fixtures(self) -> None:
        if not FIXTURE_DIR.exists():
            self.skipTest("no fixtures directory")
        cases = sorted(FIXTURE_DIR.glob("*.json"))
        if not cases:
            self.skipTest("no exported fixtures present")

        for path in cases:
            with self.subTest(path=str(path)):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("schema"), "audio-meta.release-selection-case.v1")

                mb_releases = {
                    rid: _load_release_data(rpayload) for rid, rpayload in (data.get("musicbrainz_releases") or {}).items()
                }
                daemon = _FixtureDaemon(mb_releases=mb_releases)

                pending_results: list[PendingResult] = []
                for entry in data.get("pending_results") or []:
                    meta_payload = entry.get("meta") or {}
                    meta = TrackMetadata(path=Path(entry.get("path") or "/tmp/track"))
                    meta.title = meta_payload.get("title")
                    meta.album = meta_payload.get("album")
                    meta.artist = meta_payload.get("artist")
                    meta.album_artist = meta_payload.get("album_artist")
                    meta.composer = meta_payload.get("composer")
                    meta.work = meta_payload.get("work")
                    meta.movement = meta_payload.get("movement")
                    meta.genre = meta_payload.get("genre")
                    meta.duration_seconds = meta_payload.get("duration_seconds")
                    meta.musicbrainz_release_id = meta_payload.get("musicbrainz_release_id")
                    meta.musicbrainz_track_id = meta_payload.get("musicbrainz_track_id")
                    meta.match_confidence = meta_payload.get("match_confidence")
                    meta.extra = dict(meta_payload.get("extra") or {})
                    pending_results.append(
                        PendingResult(
                            meta=meta,
                            result=None,
                            matched=bool(entry.get("matched")),
                            existing_tags=dict(entry.get("existing_tags") or {}),
                        )
                    )

                release_examples = {
                    k: ReleaseExample(**v) for k, v in (data.get("release_examples") or {}).items()
                }
                scores = {k: float(v) for k, v in (data.get("release_scores") or {}).items()}
                discogs_details = dict(data.get("discogs_details") or {})

                expected = data.get("expected") or {}
                forced_key = expected.get("forced_release_key")
                forced_provider = None
                forced_id = None
                if forced_key:
                    if ":" in forced_key:
                        forced_provider, forced_id = forced_key.split(":", 1)
                    else:
                        forced_provider, forced_id = "musicbrainz", forced_key

                decision = decide_release(
                    daemon,
                    Path(data.get("directory") or "/tmp/dir"),
                    int(data.get("file_count") or 0),
                    bool(data.get("is_singleton")),
                    int(data.get("dir_track_count") or 0),
                    data.get("dir_year"),
                    pending_results,
                    scores,
                    release_examples,
                    discogs_details,
                    forced_provider,
                    forced_id,
                    1.0 if forced_key else 0.0,
                    False,
                    False,
                )
                self.assertEqual(decision.best_release_id, expected.get("best_release_id"))
                self.assertEqual(bool(decision.should_abort), bool(expected.get("should_abort")))


if __name__ == "__main__":
    unittest.main()

