import unittest

from audio_meta.config import ProviderSettings
from audio_meta.providers.musicbrainz import MusicBrainzClient


class TestMusicBrainzReleaseTrackNumbers(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MusicBrainzClient(
            ProviderSettings(acoustid_api_key="x", musicbrainz_useragent="test")
        )

    def _numbers_from_payload(
        self, track_numbers: list[str | None]
    ) -> list[int | None]:
        release = {
            "id": "release-1",
            "title": "Transmission / Novelty",
            "date": "1979",
            "artist-credit": [{"name": "Joy Division"}],
            "medium-list": [
                {
                    "format": '7" Vinyl',
                    "track-list": [
                        {
                            "number": num,
                            "length": "210000",
                            "recording": {"id": f"rec-{i}", "title": f"Track {i}"},
                        }
                        for i, num in enumerate(track_numbers, start=1)
                    ],
                }
            ],
        }
        data = self.client._build_release_data(release)
        return [t.number for t in data.tracks]

    def test_letter_only_sides_become_sequential(self) -> None:
        self.assertEqual(self._numbers_from_payload(["A", "B"]), [1, 2])

    def test_side_prefixed_duplicates_become_sequential(self) -> None:
        self.assertEqual(self._numbers_from_payload(["A1", "B1"]), [1, 2])

    def test_disc_track_compound_numbers_use_track_component(self) -> None:
        self.assertEqual(self._numbers_from_payload(["1-1", "1-2"]), [1, 2])

    def test_missing_numbers_fill_gaps(self) -> None:
        self.assertEqual(self._numbers_from_payload(["1", None, None]), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
