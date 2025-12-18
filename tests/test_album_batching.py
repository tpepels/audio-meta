import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from audio_meta.album_batching import AlbumBatcher
from audio_meta.config import LibrarySettings
from audio_meta.scanner import DirectoryBatch, LibraryScanner


class TestAlbumBatching(unittest.TestCase):
    def test_album_root_disc_folder(self) -> None:
        self.assertEqual(AlbumBatcher.album_root(Path("/music/Album/CD1")), Path("/music/Album"))
        self.assertEqual(AlbumBatcher.album_root(Path("/music/Album/Disc 2")), Path("/music/Album"))
        self.assertEqual(AlbumBatcher.album_root(Path("/music/Album")), Path("/music/Album"))

    def test_prepare_album_batch_aggregates_disc_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            album = root / "Artist" / "Album"
            disc1 = album / "CD1"
            disc2 = album / "Disc 2"
            disc1.mkdir(parents=True)
            disc2.mkdir(parents=True)

            (disc1 / "01.mp3").write_bytes(b"")
            (disc2 / "02.mp3").write_bytes(b"")

            scanner = LibraryScanner(LibrarySettings(roots=[root], include_extensions=[".mp3"], exclude_patterns=[]))
            batcher = AlbumBatcher(scanner=scanner, processed_albums=set())

            batch = DirectoryBatch(directory=disc1, files=[disc1 / "01.mp3"])
            result = batcher.prepare_album_batch(batch)

            self.assertFalse(result.already_processed)
            self.assertIsNotNone(result.batch)
            self.assertEqual(result.batch.directory, album)
            self.assertEqual(sorted(p.name for p in result.batch.files), ["01.mp3", "02.mp3"])

    def test_prepare_album_batch_marks_processed_and_can_force(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            album = root / "Artist" / "Album"
            disc1 = album / "CD1"
            disc1.mkdir(parents=True)
            (disc1 / "01.mp3").write_bytes(b"")

            scanner = LibraryScanner(LibrarySettings(roots=[root], include_extensions=[".mp3"], exclude_patterns=[]))
            batcher = AlbumBatcher(scanner=scanner, processed_albums=set())
            batch = DirectoryBatch(directory=disc1, files=[disc1 / "01.mp3"])

            first = batcher.prepare_album_batch(batch)
            self.assertIsNotNone(first.batch)

            second = batcher.prepare_album_batch(batch)
            self.assertIsNone(second.batch)
            self.assertTrue(second.already_processed)

            forced = batcher.prepare_album_batch(batch, force_prompt=True)
            self.assertIsNotNone(forced.batch)
            self.assertFalse(forced.already_processed)


if __name__ == "__main__":
    unittest.main()

