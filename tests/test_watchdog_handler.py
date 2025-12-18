import unittest
from pathlib import Path

from audio_meta.scanner import DirectoryBatch
from audio_meta.watchdog_handler import WatchHandler


class _ScannerStub:
    def __init__(self) -> None:
        self.seen: list[Path] = []

    def collect_directory(self, directory: Path):
        self.seen.append(directory)
        return DirectoryBatch(directory=directory, files=[directory / "x.mp3"])


class _Event:
    def __init__(self, src_path, *, is_directory: bool = False) -> None:
        self.src_path = src_path
        self.is_directory = is_directory


class TestWatchdogHandler(unittest.IsolatedAsyncioTestCase):
    async def test_bytes_src_path_is_decoded(self) -> None:
        import asyncio

        queue: asyncio.Queue[DirectoryBatch] = asyncio.Queue()
        scanner = _ScannerStub()
        loop = asyncio.get_running_loop()
        handler = WatchHandler(queue, exts=[".mp3"], scanner=scanner, loop=loop)  # type: ignore[arg-type]

        handler._maybe_enqueue(_Event(b"/music/Artist/Album/01.mp3"))  # type: ignore[arg-type]

        batch = await asyncio.wait_for(queue.get(), timeout=1.0)
        self.assertEqual(scanner.seen, [Path("/music/Artist/Album")])
        self.assertEqual(batch.directory, Path("/music/Artist/Album"))


if __name__ == "__main__":
    unittest.main()
