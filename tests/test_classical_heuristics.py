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

    def test_adapt_metadata_uses_album_artist_as_performer_when_composer_present(
        self,
    ) -> None:
        heur = ClassicalHeuristics(
            ClassicalSettings(
                genre_keywords=["classical"],
                min_duration_seconds=10,
            )
        )
        meta = TrackMetadata(
            path=Path("/music/Ravel/01.flac"),
            genre="Classical",
            title="Pavane",
            album_artist="Martha Argerich",
            artist="Maurice Ravel",
            composer="Maurice Ravel",
            duration_seconds=120,
        )
        self.assertTrue(heur.adapt_metadata(meta))
        self.assertEqual(meta.album_artist, "Maurice Ravel")
        self.assertIn("Martha Argerich", meta.artist or "")

    def test_adapt_metadata_prefers_performers_then_conductor(self) -> None:
        heur = ClassicalHeuristics(
            ClassicalSettings(
                genre_keywords=["classical"],
                min_duration_seconds=10,
            )
        )
        meta = TrackMetadata(
            path=Path("/music/Ravel/01.flac"),
            genre="Classical",
            title="Concerto",
            composer="Maurice Ravel",
            album_artist="Maurice Ravel",
            artist="Maurice Ravel",
            duration_seconds=120,
        )
        meta.performers = ["Martha Argerich", "Berliner Philharmoniker"]
        meta.conductor = "Claudio Abbado"
        self.assertTrue(heur.adapt_metadata(meta))
        self.assertEqual(
            meta.artist, "Martha Argerich; Berliner Philharmoniker; Claudio Abbado"
        )


if __name__ == "__main__":
    unittest.main()
