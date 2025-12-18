import unittest

from audio_meta.match_utils import (
    normalize_match_text,
    normalize_title_for_match,
    parse_discogs_duration,
    title_similarity,
)


class TestMatchUtilsOutliers(unittest.TestCase):
    def test_normalize_match_text_strips_diacritics_and_punctuation(self) -> None:
        self.assertEqual(
            normalize_match_text("Études, Op. 10 & Op. 25!"), "etudes op 10 op 25"
        )

    def test_normalize_title_for_match_strips_leading_track_numbers(self) -> None:
        self.assertEqual(normalize_title_for_match("01 - Prelude"), "prelude")
        self.assertEqual(normalize_title_for_match("1. Prelude"), "prelude")
        self.assertEqual(normalize_title_for_match("10_Prelude"), "prelude")
        self.assertEqual(normalize_title_for_match("003 Prelude"), "prelude")

    def test_normalize_title_for_match_strips_suffix_qualifiers(self) -> None:
        self.assertEqual(normalize_title_for_match("Track (Remastered)"), "track")
        self.assertEqual(normalize_title_for_match("Track - Live"), "track")
        self.assertEqual(normalize_title_for_match("Track [Deluxe]"), "track")

    def test_normalize_title_for_match_normalizes_roman_numerals(self) -> None:
        self.assertEqual(normalize_title_for_match("Symphony No. V"), "symphony no 5")
        self.assertEqual(
            normalize_title_for_match("Part IV: Allegro"), "part 4 allegro"
        )

    def test_title_similarity_handles_roman_numerals(self) -> None:
        ratio = title_similarity("Symphony No. V", "Symphony No. 5")
        self.assertIsNotNone(ratio)
        assert ratio is not None
        self.assertGreaterEqual(ratio, 0.95)

    def test_parse_discogs_duration(self) -> None:
        self.assertEqual(parse_discogs_duration("3:05"), 185)
        self.assertEqual(parse_discogs_duration("4:30.0"), 270)
        self.assertIsNone(parse_discogs_duration(None))
        self.assertIsNone(parse_discogs_duration("3"))
        self.assertIsNone(parse_discogs_duration("3:xx"))


if __name__ == "__main__":
    unittest.main()
