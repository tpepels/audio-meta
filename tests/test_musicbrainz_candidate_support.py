import unittest
from pathlib import Path

from audio_meta.daemon_types import PendingResult
from audio_meta.models import TrackMetadata
from audio_meta.pipeline.contexts import DirectoryContext
from audio_meta.pipeline.plugins.candidates_musicbrainz import (
    MusicBrainzCandidateSourcePlugin,
)
from audio_meta.providers.musicbrainz import LookupResult


class _Services:
    @staticmethod
    def release_key(provider: str, release_id: str) -> str:
        return f"{provider}:{release_id}"


class _ReleaseTracker:
    releases: dict = {}


class _MusicBrainzStub:
    release_tracker = _ReleaseTracker()


class _DaemonStub:
    services = _Services()
    musicbrainz = _MusicBrainzStub()


class TestMusicBrainzCandidateSupport(unittest.TestCase):
    def test_single_track_match_is_downweighted_for_album_dirs(self) -> None:
        daemon = _DaemonStub()
        meta = TrackMetadata(path=Path("/music/Album/01.flac"))
        meta.musicbrainz_release_id = "r1"
        pending = PendingResult(
            meta=meta,
            result=LookupResult(track=meta, score=1.0),
            matched=True,
            existing_tags={},
        )
        ctx = DirectoryContext(
            daemon=daemon,
            directory=Path("/music/Album"),
            files=[Path("/music/Album/01.flac")] * 6,
            force_prompt=False,
            is_singleton=False,
        )
        ctx.dir_track_count = 6
        ctx.pending_results = [pending]

        plugin = MusicBrainzCandidateSourcePlugin()
        plugin.add(ctx)

        self.assertIn("musicbrainz:r1", ctx.release_scores)
        self.assertLess(ctx.release_scores["musicbrainz:r1"], 0.3)

    def test_singleton_directory_keeps_full_score(self) -> None:
        daemon = _DaemonStub()
        meta = TrackMetadata(path=Path("/music/Single/track.flac"))
        meta.musicbrainz_release_id = "r1"
        pending = PendingResult(
            meta=meta,
            result=LookupResult(track=meta, score=1.0),
            matched=True,
            existing_tags={},
        )
        ctx = DirectoryContext(
            daemon=daemon,
            directory=Path("/music/Single"),
            files=[Path("/music/Single/track.flac")],
            force_prompt=False,
            is_singleton=True,
        )
        ctx.dir_track_count = 1
        ctx.pending_results = [pending]

        plugin = MusicBrainzCandidateSourcePlugin()
        plugin.add(ctx)

        self.assertEqual(ctx.release_scores["musicbrainz:r1"], 1.0)


if __name__ == "__main__":
    unittest.main()

