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
            self.assertIn("Deferred prompts: OK", joined)
            self.assertIn("1 pending", joined)

    def test_reports_error_when_organizer_enabled_without_target_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            root = tmp / "music"
            root.mkdir()
            cache_path = tmp / "cache.sqlite3"
            MetadataCache(cache_path).close()

            settings = Settings(
                library=LibrarySettings(roots=[root]),
                providers=ProviderSettings(
                    acoustid_api_key="x", musicbrainz_useragent="x"
                ),
                organizer=OrganizerSettings(enabled=True, target_root=None),
                daemon=DaemonSettings(cache_path=cache_path),
            )

            report = run(settings)
            self.assertFalse(report.ok)
            self.assertIn("Organizer: ERROR", "\n".join(report.checks))

    def test_warns_on_large_deferred_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            root = tmp / "music"
            root.mkdir()
            cache_path = tmp / "cache.sqlite3"

            cache = MetadataCache(cache_path)
            try:
                for idx in range(60):
                    cache.add_deferred_prompt(root / f"D{idx}", "ambiguous_release")
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
            joined = "\n".join(report.checks)
            self.assertIn("Deferred prompts: WARNING", joined)
            self.assertIn("60 pending", joined)

    def test_reports_recent_provider_warnings_from_scan_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            root = tmp / "music"
            root.mkdir()
            cache_path = tmp / "cache.sqlite3"
            warn_log = tmp / "audio-meta-warnings.log"
            warn_log.write_text(
                "socket.gaierror: [Errno -3] Temporary failure in name resolution\n",
                encoding="utf-8",
            )

            cache = MetadataCache(cache_path)
            try:
                cache.append_audit_event(
                    "scan_complete",
                    {
                        "skipped_directories": 0,
                        "deferred_prompts": 0,
                        "warning_lines": 1,
                        "warning_log_path": str(warn_log),
                    },
                )
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
            self.assertIn(
                "Providers (recent warnings): WARNING", "\n".join(report.checks)
            )


if __name__ == "__main__":
    unittest.main()
