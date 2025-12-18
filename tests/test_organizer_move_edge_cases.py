import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_meta.config import LibrarySettings, OrganizerSettings
from audio_meta.models import TrackMetadata
from audio_meta.organizer import Organizer


class TestOrganizerMoveEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.organizer = Organizer(
            OrganizerSettings(enabled=True),
            LibrarySettings(roots=[Path("/music")], include_extensions=[".mp3"]),
        )

    def test_move_falls_back_to_shutil_move_on_exdev(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / "src"
            dst_dir = Path(tmp) / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()
            src = src_dir / "01.mp3"
            dst = dst_dir / "01.mp3"
            src.write_bytes(b"hello")
            meta = TrackMetadata(path=src)

            orig_move = __import__("shutil").move
            orig_rename = Path.rename

            def rename_side_effect(self: Path, target: Path):
                if self == src:
                    raise OSError(errno.EXDEV, "Cross-device link")
                return orig_rename(self, target)

            with (
                patch("pathlib.Path.rename", rename_side_effect),
                patch("audio_meta.organizer.shutil.move", side_effect=lambda s, d: orig_move(s, d)),
            ):
                self.organizer.move(meta, dst, dry_run=False)

            self.assertFalse(src.exists())
            self.assertTrue(dst.exists())
            self.assertEqual(meta.path, dst)

    def test_move_failure_keeps_path_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / "src"
            dst_dir = Path(tmp) / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()
            src = src_dir / "01.mp3"
            dst = dst_dir / "01.mp3"
            src.write_bytes(b"hello")
            meta = TrackMetadata(path=src)

            orig_rename = Path.rename

            def rename_side_effect(self: Path, target: Path):
                if self == src:
                    raise OSError(errno.EACCES, "Permission denied")
                return orig_rename(self, target)

            with patch("pathlib.Path.rename", rename_side_effect):
                self.organizer.move(meta, dst, dry_run=False)

            self.assertTrue(src.exists())
            self.assertFalse(dst.exists())
            self.assertEqual(meta.path, src)


if __name__ == "__main__":
    unittest.main()
