import unittest
from pathlib import Path
from types import SimpleNamespace

from audio_meta.daemon_types import ReleaseExample
from audio_meta.pipeline.contexts import DirectoryContext
from audio_meta.pipeline.plugins.candidates_discogs import DiscogsCandidateSourcePlugin


class TestDiscogsCandidatesSyntheticSampleMeta(unittest.TestCase):
    def test_discogs_candidates_use_files_when_no_pending_results(self) -> None:
        seen: list[str] = []

        def discogs_candidates(meta):
            seen.append(str(meta.path))
            return [
                {
                    "id": "123",
                    "title": "Album",
                    "artist": "Artist",
                    "year": "2000",
                    "track_count": 10,
                    "disc_count": 1,
                    "formats": ["CD"],
                    "score": 0.6,
                }
            ]

        daemon = SimpleNamespace(
            discogs=object(),
            services=SimpleNamespace(
                discogs_candidates=discogs_candidates,
                release_key=lambda provider, release_id: f"{provider}:{release_id}",
            ),
        )
        ctx = DirectoryContext(
            daemon=daemon,
            directory=Path("/music/Artist/Album"),
            files=[Path("/music/Artist/Album/01.flac")],
            force_prompt=False,
            dir_track_count=1,
        )
        ctx.release_examples = {}
        ctx.release_scores = {}
        ctx.discogs_details = {}

        DiscogsCandidateSourcePlugin().add(ctx)

        self.assertEqual(seen, ["/music/Artist/Album/01.flac"])
        self.assertIn("discogs:123", ctx.release_scores)
        self.assertIsInstance(ctx.release_examples["discogs:123"], ReleaseExample)


if __name__ == "__main__":
    unittest.main()
