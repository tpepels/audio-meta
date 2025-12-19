"""
Unit tests for audio_meta.core.identity.matching module.

Tests all matching strategies:
- Token normalization
- Exact matching
- Substring matching
- Initial matching (e.g., "J.S. Bach" vs "Johann Sebastian Bach")
- NameMatcher class integration
"""

import unittest

from audio_meta.core.identity.matching import (
    NameMatcher,
    extract_initials,
    extract_words,
    match_exact,
    match_initials,
    match_substring,
    normalize_token,
)


class TestNormalizeToken(unittest.TestCase):
    """Test token normalization."""

    def test_basic_normalization(self):
        self.assertEqual(normalize_token("Ludwig van Beethoven"), "ludwigvanbeethoven")

    def test_removes_punctuation(self):
        self.assertEqual(normalize_token("Yo-Yo Ma"), "yoyoma")
        self.assertEqual(normalize_token("Art Blakey & The Jazz Messengers"), "artblakeythejazzmessengers")

    def test_removes_featuring_patterns(self):
        self.assertEqual(normalize_token("Artist (feat. Guest)"), "artist")
        self.assertEqual(normalize_token("Artist featuring Guest"), "artist")
        self.assertEqual(normalize_token("Artist ft. Guest"), "artist")
        self.assertEqual(normalize_token("Artist with Guest"), "artist")

    def test_unicode_normalization(self):
        # Accents should be removed
        self.assertEqual(normalize_token("Dvořák"), "dvorak")
        self.assertEqual(normalize_token("Brüggen"), "bruggen")

    def test_collapse_spaces(self):
        self.assertEqual(normalize_token("Too   Many    Spaces"), "toomanyspaces")

    def test_empty_string(self):
        self.assertEqual(normalize_token(""), "")

    def test_preserves_numbers(self):
        self.assertEqual(normalize_token("Blink 182"), "blink182")


class TestExtractWords(unittest.TestCase):
    """Test word extraction."""

    def test_basic_extraction(self):
        self.assertEqual(extract_words("ludwig van beethoven"), ["ludwig", "van", "beethoven"])

    def test_filters_short_words(self):
        # extract_words does NOT filter short words - it includes all parts
        words = extract_words("j s bach")
        # All parts should be included
        self.assertIn("j", words)
        self.assertIn("s", words)
        self.assertIn("bach", words)

    def test_handles_empty_string(self):
        self.assertEqual(extract_words(""), [])


class TestExtractInitials(unittest.TestCase):
    """Test initial extraction."""

    def test_basic_initials(self):
        words = ["johann", "sebastian", "bach"]
        # extract_initials just takes first letter of each word
        self.assertEqual(extract_initials(words), "jsb")

    def test_handles_empty_list(self):
        self.assertEqual(extract_initials([]), "")

    def test_single_word(self):
        # extract_initials just takes first letter
        self.assertEqual(extract_initials(["bach"]), "b")


class TestMatchExact(unittest.TestCase):
    """Test exact token matching."""

    def test_exact_match(self):
        result = match_exact("beethoven", "beethoven")
        self.assertTrue(result.matches)
        self.assertEqual(result.confidence, 1.0)

    def test_no_match(self):
        result = match_exact("beethoven", "mozart")
        self.assertFalse(result.matches)


class TestMatchSubstring(unittest.TestCase):
    """Test substring matching."""

    def test_substring_match(self):
        result = match_substring("beethoven", "ludwigvanbeethoven")
        self.assertTrue(result.matches)
        self.assertGreater(result.confidence, 0.8)

    def test_reverse_substring_no_match(self):
        # match_substring expects short_token first, long_token second
        # If you reverse them, it won't match
        result = match_substring("ludwigvanbeethoven", "beethoven")
        self.assertFalse(result.matches)

    def test_no_substring_match(self):
        result = match_substring("beethoven", "mozart")
        self.assertFalse(result.matches)

    def test_partial_substring_matches(self):
        # Substring matching doesn't have a minimum length threshold
        # It just checks if short is contained in long
        result = match_substring("abc", "abcdefghijk")
        self.assertTrue(result.matches)


class TestMatchInitials(unittest.TestCase):
    """Test initial matching (e.g., J.S. Bach vs Johann Sebastian Bach)."""

    def test_js_bach_matches_johann_sebastian_bach(self):
        # Note: extract_words expects original names (with spaces/dots), not normalized tokens
        short_name = "J.S. Bach"
        long_name = "Johann Sebastian Bach"
        short = normalize_token(short_name)
        long = normalize_token(long_name)
        short_words = extract_words(short_name)
        long_words = extract_words(long_name)

        result = match_initials(short, long, short_words, long_words)
        self.assertTrue(result.matches, "J.S. Bach should match Johann Sebastian Bach")
        self.assertGreaterEqual(result.confidence, 0.90)

    def test_wa_mozart_matches_wolfgang_amadeus_mozart(self):
        short_name = "W.A. Mozart"
        long_name = "Wolfgang Amadeus Mozart"
        short = normalize_token(short_name)
        long = normalize_token(long_name)
        short_words = extract_words(short_name)
        long_words = extract_words(long_name)

        result = match_initials(short, long, short_words, long_words)
        self.assertTrue(result.matches)
        self.assertGreaterEqual(result.confidence, 0.90)

    def test_lv_beethoven_matches_ludwig_van_beethoven(self):
        short_name = "L.v. Beethoven"
        long_name = "Ludwig van Beethoven"
        short = normalize_token(short_name)
        long = normalize_token(long_name)
        short_words = extract_words(short_name)
        long_words = extract_words(long_name)

        result = match_initials(short, long, short_words, long_words)
        self.assertTrue(result.matches)
        self.assertGreaterEqual(result.confidence, 0.90)

    def test_no_match_different_names(self):
        short_name = "J.S. Bach"
        long_name = "Wolfgang Amadeus Mozart"
        short = normalize_token(short_name)
        long = normalize_token(long_name)
        short_words = extract_words(short_name)
        long_words = extract_words(long_name)

        result = match_initials(short, long, short_words, long_words)
        self.assertFalse(result.matches)

    def test_no_match_if_short_is_actually_longer(self):
        # Should not match if "short" token is longer than "long"
        short_name = "Johann Sebastian Bach"
        long_name = "J.S. Bach"
        short = normalize_token(short_name)
        long = normalize_token(long_name)
        short_words = extract_words(short_name)
        long_words = extract_words(long_name)

        result = match_initials(short, long, short_words, long_words)
        self.assertFalse(result.matches)


class TestNameMatcher(unittest.TestCase):
    """Test the NameMatcher class - basic name-to-name matching."""

    def test_exact_match(self):
        matcher = NameMatcher()
        result = matcher.match("Beethoven", "Beethoven")
        self.assertTrue(result.matches)
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.strategy, "exact")

    def test_substring_match(self):
        matcher = NameMatcher()
        result = matcher.match("Beethoven", "Ludwig van Beethoven")
        self.assertTrue(result.matches)
        self.assertEqual(result.confidence, 0.85)
        self.assertEqual(result.strategy, "substring")

    def test_initial_match(self):
        matcher = NameMatcher()
        result = matcher.match("J.S. Bach", "Johann Sebastian Bach")
        self.assertTrue(result.matches)
        self.assertGreaterEqual(result.confidence, 0.90)
        self.assertIn("initial", result.strategy)

    def test_no_match(self):
        matcher = NameMatcher()
        result = matcher.match("Beethoven", "Mozart")
        self.assertFalse(result.matches)


if __name__ == "__main__":
    unittest.main()
