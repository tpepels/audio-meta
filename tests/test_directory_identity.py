import unittest
from pathlib import Path

from audio_meta.directory_identity import (
    hint_cache_key,
    looks_like_disc_folder,
    normalize_hint_value,
    path_based_hints,
    tokenize,
    token_overlap_ratio,
)


class TestDirectoryIdentity(unittest.TestCase):
    def test_looks_like_disc_folder(self) -> None:
        self.assertTrue(looks_like_disc_folder("CD1"))
        self.assertTrue(looks_like_disc_folder("Disc 2"))
        self.assertTrue(looks_like_disc_folder("disk03"))
        self.assertFalse(looks_like_disc_folder("Discography"))
        self.assertFalse(looks_like_disc_folder("Album"))

    def test_path_based_hints_skip_disc_folder(self) -> None:
        directory = Path("/music/Artist/Album/CD1")
        artist, album = path_based_hints(directory)
        self.assertEqual(artist, "Artist")
        self.assertEqual(album, "Album")

        directory = Path("/music/Artist/Album/Disc 1")
        artist, album = path_based_hints(directory)
        self.assertEqual(artist, "Artist")
        self.assertEqual(album, "Album")

        directory = Path("/music/Artist/Album")
        artist, album = path_based_hints(directory)
        self.assertEqual(artist, "Artist")
        self.assertEqual(album, "Album")

    def test_normalize_hint_value_strips_accents(self) -> None:
        self.assertEqual(normalize_hint_value("Frédéric Chopin"), "frederic chopin")
        self.assertEqual(normalize_hint_value("Études op. 10 & op. 25"), "etudes op 10 op 25")

    def test_hint_cache_key_requires_album(self) -> None:
        self.assertIsNone(hint_cache_key("Artist", None))
        self.assertEqual(hint_cache_key(None, "Album"), "hint://unknown|album")

    def test_tokenize_and_overlap(self) -> None:
        self.assertEqual(tokenize("A/B - C"), ["a", "b", "c"])
        self.assertAlmostEqual(token_overlap_ratio("frederic chopin", "Chopin, Frédéric"), 1.0)


if __name__ == "__main__":
    unittest.main()
