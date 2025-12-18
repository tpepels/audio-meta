import tempfile
import unittest
from pathlib import Path

from audio_meta.fs_utils import MAX_BASENAME_BYTES, fit_destination_path, path_exists


class TestFsUtils(unittest.TestCase):
    def test_path_exists_true_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            p = tmp / "file.txt"
            self.assertEqual(path_exists(p), False)
            p.write_text("x", encoding="utf-8")
            self.assertEqual(path_exists(p), True)

    def test_path_exists_returns_none_when_parent_missing(self) -> None:
        p = Path("/this/path/does/not/exist/file.txt")
        self.assertEqual(path_exists(p), False)

    def test_fit_destination_path_truncates_long_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            long_stem = "a" * (MAX_BASENAME_BYTES + 50)
            p = tmp / f"{long_stem}.flac"
            fitted = fit_destination_path(p)
            self.assertEqual(fitted.parent, tmp)
            self.assertEqual(fitted.suffix, ".flac")
            self.assertLessEqual(len(fitted.name.encode("utf-8")), MAX_BASENAME_BYTES)

    def test_fit_destination_path_avoids_existing_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            base = tmp / ("b" * (MAX_BASENAME_BYTES + 50) + ".mp3")
            first = fit_destination_path(base)
            first.write_bytes(b"x")
            second = fit_destination_path(base)
            self.assertNotEqual(first.name, second.name)


if __name__ == "__main__":
    unittest.main()
