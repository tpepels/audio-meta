import unittest
from pathlib import Path

from audio_meta.assignment_diagnostics import build_assignment_diagnostics
from audio_meta.daemon_types import PendingResult, ReleaseExample
from audio_meta.directory_identity import token_overlap_ratio
from audio_meta.models import TrackMetadata
from audio_meta.release_prompt import build_release_prompt_options


class _DaemonStub:
    @staticmethod
    def _split_release_key(key: str) -> tuple[str, str]:
        provider, rid = key.split(":", 1)
        return provider, rid

    @staticmethod
    def _parse_year(value):
        if not value:
            return None
        return int(str(value)[:4]) if str(value)[:4].isdigit() else None

    def _token_overlap_ratio(self, expected, candidate):
        return token_overlap_ratio(expected, candidate)

    @staticmethod
    def _disc_label(_n):
        return None

    @staticmethod
    def _format_option_label(
        idx,
        provider_tag,
        artist,
        title,
        year,
        track_count,
        disc_label,
        format_label,
        score,
        release_id,
    ):
        return f"{idx}:{provider_tag}:{artist}:{title}:{year}:{track_count}:{release_id}:{score}"

    def _release_track_entries(self, key, _examples, _discogs_details):
        if key == "musicbrainz:good":
            return [
                ("track one", 100),
                ("track two", 120),
            ]
        if key == "musicbrainz:badmeta":
            return [
                ("something else", 100),
            ]
        return None


class TestAssignmentDiagnostics(unittest.TestCase):
    def test_build_assignment_diagnostics_adds_consensus(self) -> None:
        daemon = _DaemonStub()
        pending = [
            PendingResult(
                meta=TrackMetadata(path=Path("/x/01.flac"), title="Track One", duration_seconds=100),
                result=None,
                matched=False,
                existing_tags={},
            ),
            PendingResult(
                meta=TrackMetadata(path=Path("/x/02.flac"), title="Track Two", duration_seconds=120),
                result=None,
                matched=False,
                existing_tags={},
            ),
        ]
        candidates = [("musicbrainz:good", 1.0)]
        examples = {
            "musicbrainz:good": ReleaseExample(
                provider="musicbrainz",
                title="Album",
                artist="Various Artists",
                date="2001",
                track_total=2,
                disc_count=1,
                formats=["CD"],
            )
        }
        diag = build_assignment_diagnostics(
            daemon,
            candidates=candidates,
            pending_results=pending,
            release_examples=examples,
            discogs_details={},
            dir_track_count=2,
            dir_year=2001,
            tag_hints={"artist": ["Various Artists"], "album": ["Album"]},
        )
        self.assertIn("musicbrainz:good", diag)
        self.assertEqual(diag["musicbrainz:good"].matched, 2)
        self.assertAlmostEqual(diag["musicbrainz:good"].coverage or 0.0, 1.0)
        self.assertAlmostEqual(diag["musicbrainz:good"].consensus or 0.0, 1.0)

        opts = build_release_prompt_options(
            candidates,
            examples,
            split_release_key=daemon._split_release_key,
            parse_year=daemon._parse_year,
            disc_label=daemon._disc_label,
            format_option_label=daemon._format_option_label,
            show_urls=False,
            diagnostics=diag,
        )
        self.assertIn("cons", opts[0].label)

    def test_build_assignment_diagnostics_includes_reasons(self) -> None:
        daemon = _DaemonStub()
        pending = [
            PendingResult(
                meta=TrackMetadata(path=Path("/x/01.flac"), title="Track One", duration_seconds=100),
                result=None,
                matched=False,
                existing_tags={},
            ),
        ]
        candidates = [("musicbrainz:badmeta", 1.0)]
        examples = {
            "musicbrainz:badmeta": ReleaseExample(
                provider="musicbrainz",
                title="Totally Different Title",
                artist="Someone Else",
                date="1990",
                track_total=9,
                disc_count=1,
                formats=["CD"],
            )
        }
        diag = build_assignment_diagnostics(
            daemon,
            candidates=candidates,
            pending_results=pending,
            release_examples=examples,
            discogs_details={},
            dir_track_count=2,
            dir_year=2001,
            tag_hints={"artist": ["Various Artists"], "album": ["Album"]},
        )
        reasons = diag["musicbrainz:badmeta"].reasons
        self.assertIn("tracks 2/9", reasons)
        self.assertIn("year 2001/1990", reasons)
        self.assertIn("artist mismatch", reasons)
        self.assertIn("album mismatch", reasons)


if __name__ == "__main__":
    unittest.main()
