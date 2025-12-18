import unittest
from pathlib import Path

from audio_meta.daemon_types import PendingResult, ReleaseExample
from audio_meta.directory_identity import token_overlap_ratio
from audio_meta.models import TrackMetadata
from audio_meta.release_scoring import adjust_release_scores


class _DaemonForScoring:
    @staticmethod
    def _token_overlap_ratio(expected: str | None, candidate: str | None) -> float:
        return token_overlap_ratio(expected, candidate)

    @staticmethod
    def _parse_year(value: str | None) -> int | None:
        if not value:
            return None
        import re

        match = re.search(r"(19|20)\d{2}", value)
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    @staticmethod
    def _path_based_hints(_directory: Path):
        return None, None

    @staticmethod
    def _split_release_key(key: str):
        if ":" in key:
            provider, rid = key.split(":", 1)
            return provider, rid
        return "musicbrainz", key

    def _match_pending_to_release(self, *_args, **_kwargs):
        return None

    musicbrainz = None


class TestReleaseScoringTagHints(unittest.TestCase):
    def test_composer_hint_biases_toward_matching_release_artist(self) -> None:
        daemon = _DaemonForScoring()
        pending_results = [
            PendingResult(
                meta=TrackMetadata(path=Path("/music/x.flac")),
                result=None,
                matched=False,
                existing_tags={"composer": "Frédéric Chopin"},
            )
        ]
        release_examples = {
            "discogs:1": ReleaseExample(
                provider="discogs",
                title="Études op. 10 & op. 25",
                artist="Frédéric Chopin, Maurizio Pollini",
                date="2014",
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
            "discogs:2": ReleaseExample(
                provider="discogs",
                title="Études op. 10 & op. 25",
                artist="Murray Perahia",
                date="2002",
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
        }
        scores = {"discogs:1": 1.0, "discogs:2": 1.0}
        adjusted, _coverage = adjust_release_scores(
            daemon,
            scores=scores,
            release_examples=release_examples,
            dir_track_count=24,
            dir_year=None,
            pending_results=pending_results,
            directory=Path("/music/Frederic Chopin/Etudes"),
            discogs_details={},
        )
        self.assertGreater(adjusted["discogs:1"], adjusted["discogs:2"])

    def test_composer_hint_is_ignored_when_ambiguous(self) -> None:
        daemon = _DaemonForScoring()
        pending_results = [
            PendingResult(
                meta=TrackMetadata(path=Path("/music/01.flac")),
                result=None,
                matched=False,
                existing_tags={"composer": "Frédéric Chopin"},
            ),
            PendingResult(
                meta=TrackMetadata(path=Path("/music/02.flac")),
                result=None,
                matched=False,
                existing_tags={"composer": "J. S. Bach"},
            ),
        ]
        release_examples = {
            "discogs:chopin": ReleaseExample(
                provider="discogs",
                title="Études op. 10 & op. 25",
                artist="Frédéric Chopin, Maurizio Pollini",
                date="2014",
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
            "discogs:bach": ReleaseExample(
                provider="discogs",
                title="Études op. 10 & op. 25",
                artist="J. S. Bach, Glenn Gould",
                date="2014",
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
        }
        scores = {"discogs:chopin": 1.0, "discogs:bach": 1.0}
        adjusted, _coverage = adjust_release_scores(
            daemon,
            scores=scores,
            release_examples=release_examples,
            dir_track_count=24,
            dir_year=None,
            pending_results=pending_results,
            directory=Path("/music/Mixed/Etudes"),
            discogs_details={},
        )
        self.assertAlmostEqual(adjusted["discogs:chopin"], adjusted["discogs:bach"], places=6)

    def test_work_hint_biases_toward_matching_release_title(self) -> None:
        daemon = _DaemonForScoring()
        pending_results = [
            PendingResult(
                meta=TrackMetadata(path=Path("/music/x.flac")),
                result=None,
                matched=False,
                existing_tags={"work": "12 Etudes op. 10 - 12 Etudes op. 25"},
            )
        ]
        release_examples = {
            "discogs:good": ReleaseExample(
                provider="discogs",
                title="12 Etudes op. 10 - 12 Etudes op. 25",
                artist="Any Performer",
                date=None,
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
            "discogs:bad": ReleaseExample(
                provider="discogs",
                title="Nocturnes",
                artist="Any Performer",
                date=None,
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
        }
        scores = {"discogs:good": 1.0, "discogs:bad": 1.0}
        adjusted, _coverage = adjust_release_scores(
            daemon,
            scores=scores,
            release_examples=release_examples,
            dir_track_count=24,
            dir_year=None,
            pending_results=pending_results,
            directory=Path("/music/Frederic Chopin/Etudes"),
            discogs_details={},
        )
        self.assertGreater(adjusted["discogs:good"], adjusted["discogs:bad"])

    def test_performer_hint_is_ignored_when_ambiguous(self) -> None:
        daemon = _DaemonForScoring()
        pending_results = [
            PendingResult(
                meta=TrackMetadata(path=Path("/music/01.flac")),
                result=None,
                matched=False,
                existing_tags={"album_artist": "Maurizio Pollini", "album": "Études op. 10 & op. 25"},
            ),
            PendingResult(
                meta=TrackMetadata(path=Path("/music/02.flac")),
                result=None,
                matched=False,
                existing_tags={"album_artist": "Murray Perahia", "album": "Études op. 10 & op. 25"},
            ),
        ]
        release_examples = {
            "discogs:pollini": ReleaseExample(
                provider="discogs",
                title="Études op. 10 & op. 25",
                artist="Frédéric Chopin, Maurizio Pollini",
                date="2014",
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
            "discogs:perahia": ReleaseExample(
                provider="discogs",
                title="Études op. 10 & op. 25",
                artist="Frédéric Chopin, Murray Perahia",
                date="2002",
                track_total=24,
                disc_count=1,
                formats=["CD"],
            ),
        }
        scores = {"discogs:pollini": 1.0, "discogs:perahia": 1.0}
        adjusted, _coverage = adjust_release_scores(
            daemon,
            scores=scores,
            release_examples=release_examples,
            dir_track_count=24,
            dir_year=None,
            pending_results=pending_results,
            directory=Path("/music/Mixed/Etudes"),
            discogs_details={},
        )
        self.assertAlmostEqual(adjusted["discogs:pollini"], adjusted["discogs:perahia"], places=6)


if __name__ == "__main__":
    unittest.main()
