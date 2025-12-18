import unittest
from pathlib import Path

from audio_meta.config import LibrarySettings, OrganizerSettings
from audio_meta.models import TrackMetadata
from audio_meta.organizer import Organizer


class TestOrganizerFilename(unittest.TestCase):
    def setUp(self) -> None:
        self.organizer = Organizer(
            OrganizerSettings(enabled=True),
            LibrarySettings(roots=[Path("/music")], include_extensions=[".mp3"], exclude_patterns=[]),
        )

    def test_build_filename_parses_tracknumber_string(self) -> None:
        meta = TrackMetadata(path=Path("/music/a.mp3"), title="Song")
        meta.extra["TRACKNUMBER"] = "03/12"
        self.assertTrue(self.organizer._build_filename(meta).startswith("03 - "))

    def test_build_filename_uses_tracknumber_int(self) -> None:
        meta = TrackMetadata(path=Path("/music/a.mp3"), title="Song")
        meta.extra["TRACKNUMBER"] = 7
        self.assertTrue(self.organizer._build_filename(meta).startswith("07 - "))


if __name__ == "__main__":
    unittest.main()

