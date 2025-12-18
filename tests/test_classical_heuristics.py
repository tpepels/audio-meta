import unittest
from pathlib import Path

from audio_meta.classical import ClassicalHeuristics
from audio_meta.config import ClassicalSettings
from audio_meta.models import TrackMetadata


class TestClassicalHeuristics(unittest.TestCase):
    def test_adapt_metadata_sets_album_artist_to_composer(self) -> None:
        heur = ClassicalHeuristics(
            ClassicalSettings(
                genre_keywords=["classical"],
                min_duration_seconds=10,
            )
        )
        meta = TrackMetadata(
            path=Path("/music/Chopin/01.flac"),
            genre="Classical",
            title="Etude Op. 10 No. 1",
            artist="Maurizio Pollini",
            composer="Frédéric Chopin",
            duration_seconds=120,
        )
        self.assertTrue(heur.adapt_metadata(meta))
        self.assertEqual(meta.album_artist, "Frédéric Chopin")

    def test_adapt_metadata_infers_composer_from_album_artist(self) -> None:
        heur = ClassicalHeuristics(
            ClassicalSettings(
                genre_keywords=["classical"],
                min_duration_seconds=10,
            )
        )
        meta = TrackMetadata(
            path=Path("/music/Chopin/01.flac"),
            genre="Classical",
            title="Etude Op. 10 No. 1",
            artist="Pollini; Chopin",
            album_artist="Chopin; Pollini",
            duration_seconds=120,
        )
        self.assertTrue(heur.adapt_metadata(meta))
        self.assertEqual(meta.composer, "Chopin")


if __name__ == "__main__":
    unittest.main()

