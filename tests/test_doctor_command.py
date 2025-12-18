import tempfile
import unittest
from pathlib import Path

from audio_meta.cache import MetadataCache
from audio_meta.commands.doctor import run
from audio_meta.config import (
    DaemonSettings,
    LibrarySettings,
    OrganizerSettings,
    ProviderSettings,
    Settings,
)


class TestDoctorCommand(unittest.TestCase):
    def test_reports_discogs_disabled_and_deferred_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            root = tmp / "music"
            root.mkdir()
            cache_path = tmp / "cache.sqlite3"

            cache = MetadataCache(cache_path)
            try:
                cache.add_deferred_prompt(root / "A", "ambiguous_release")
            finally:
                cache.close()

            settings = Settings(
                library=LibrarySettings(roots=[root]),
                providers=ProviderSettings(
                    acoustid_api_key="x", musicbrainz_useragent="x"
                ),
                organizer=OrganizerSettings(enabled=False),
                daemon=DaemonSettings(cache_path=cache_path),
            )

            report = run(settings)
            self.assertTrue(report.ok)
            joined = "\n".join(report.checks)
            self.assertIn("Discogs: DISABLED", joined)
            self.assertIn("Deferred prompts: 1 pending", joined)


if __name__ == "__main__":
    unittest.main()
