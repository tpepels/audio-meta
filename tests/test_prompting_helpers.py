import unittest

from audio_meta.prompting import (
    invalid_release_choice_message,
    manual_release_choice_help,
    release_url,
)


class TestPromptingHelpers(unittest.TestCase):
    def test_release_url(self) -> None:
        self.assertEqual(
            release_url("musicbrainz", "df26158f-1cba-45b9-b54c-1d2857a41d2b"),
            "https://musicbrainz.org/release/df26158f-1cba-45b9-b54c-1d2857a41d2b",
        )
        self.assertEqual(
            release_url("discogs", "123"),
            "https://www.discogs.com/release/123",
        )
        self.assertIsNone(release_url("discogs", "not-a-number"))
        self.assertIsNone(release_url("other", "x"))

    def test_manual_choice_help(self) -> None:
        self.assertIn("dg:", manual_release_choice_help(discogs_enabled=True))
        self.assertNotIn("dg:", manual_release_choice_help(discogs_enabled=False))

    def test_invalid_choice_message(self) -> None:
        self.assertIn("mb:/dg:", invalid_release_choice_message(discogs_enabled=True))
        self.assertIn("mb:", invalid_release_choice_message(discogs_enabled=False))
        self.assertNotIn("dg:", invalid_release_choice_message(discogs_enabled=False))


if __name__ == "__main__":
    unittest.main()
