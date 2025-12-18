import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_meta.config import (
    DaemonSettings,
    LibrarySettings,
    OrganizerSettings,
    ProviderSettings,
    Settings,
)
from audio_meta.daemon import AudioMetaDaemon
from audio_meta.daemon_types import ReleaseExample


class TestInteractivePromptRendering(unittest.TestCase):
    def test_low_coverage_prompt_includes_mb_url_and_hides_discogs_hint(self) -> None:
        settings = Settings(
            library=LibrarySettings(roots=[Path("/music")]),
            providers=ProviderSettings(acoustid_api_key="x", musicbrainz_useragent="x"),
            organizer=OrganizerSettings(enabled=False),
            daemon=DaemonSettings(
                prompt_show_urls=True,
                prompt_expand_mb_candidates=False,
                prompt_mb_search_limit=0,
            ),
        )
        daemon = AudioMetaDaemon(settings, interactive=False, discogs=None)
        self.addCleanup(daemon.cache.close)

        release_id = "df26158f-1cba-45b9-b54c-1d2857a41d2b"
        key = f"musicbrainz:{release_id}"
        release_examples = {
            key: ReleaseExample(
                provider="musicbrainz",
                title="Pétrouchka / Le Sacre du printemps",
                artist="The Cleveland Orchestra; Pierre Boulez",
                date="1992",
                track_total=6,
                disc_count=1,
                formats=["CD"],
            )
        }

        with (
            patch("builtins.input", return_value="0"),
            patch("sys.stdout", new_callable=io.StringIO) as buf,
        ):
            selection = daemon._resolve_release_interactively(
                Path("/music/Stravinsky/Petrouchka - Le Sacre du printemps"),
                [(key, 1.10)],
                release_examples,
                sample_meta=None,
                dir_track_count=6,
                dir_year=None,
                discogs_details={},
                prompt_title="Low-coverage match",
                coverage=0.0,
            )

        self.assertIsNone(selection)
        out = buf.getvalue()
        self.assertIn("Low-coverage match for", out)
        self.assertIn(f"https://musicbrainz.org/release/{release_id}", out)
        self.assertIn("Discogs disabled", out)
        self.assertNotIn("dg:<release-id>", out)

    def test_unmatched_directory_prompt_includes_mb_url(self) -> None:
        settings = Settings(
            library=LibrarySettings(roots=[Path("/music")]),
            providers=ProviderSettings(acoustid_api_key="x", musicbrainz_useragent="x"),
            organizer=OrganizerSettings(enabled=False),
            daemon=DaemonSettings(
                prompt_show_urls=True,
                prompt_mb_search_limit=6,
            ),
        )

        class _MusicBrainzStub:
            def search_release_candidates(self, _artist, _album, *, limit: int = 6):
                assert limit == 6
                return [
                    {
                        "id": "df26158f-1cba-45b9-b54c-1d2857a41d2b",
                        "artist": "Artist",
                        "title": "Album",
                        "date": "2001",
                        "track_total": 10,
                        "disc_count": 1,
                        "formats": ["CD"],
                        "score": 0.9,
                    }
                ]

        daemon = AudioMetaDaemon(
            settings, interactive=False, discogs=None, musicbrainz=_MusicBrainzStub()
        )
        self.addCleanup(daemon.cache.close)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file_path = directory / "01.flac"
            file_path.write_bytes(b"")

            with (
                patch("builtins.input", return_value="0"),
                patch("sys.stdout", new_callable=io.StringIO) as buf,
            ):
                selection = daemon._resolve_unmatched_directory(
                    directory,
                    sample_meta=None,
                    dir_track_count=2,
                    dir_year=None,
                    files=[file_path],
                )

        self.assertIsNone(selection)
        out = buf.getvalue()
        self.assertIn("No automatic metadata match for", out)
        self.assertIn(
            "https://musicbrainz.org/release/df26158f-1cba-45b9-b54c-1d2857a41d2b", out
        )
        self.assertNotIn("dg:<release-id>", out)

    def test_prompt_shows_sample_track_preview(self) -> None:
        settings = Settings(
            library=LibrarySettings(roots=[Path("/music")]),
            providers=ProviderSettings(acoustid_api_key="x", musicbrainz_useragent="x"),
            organizer=OrganizerSettings(enabled=False),
            daemon=DaemonSettings(
                prompt_show_urls=True,
                prompt_mb_search_limit=1,
                prompt_preview_tracks=2,
            ),
        )

        class _MusicBrainzStub:
            def search_release_candidates(self, _artist, _album, *, limit: int = 6):
                return [
                    {
                        "id": "df26158f-1cba-45b9-b54c-1d2857a41d2b",
                        "artist": "Artist",
                        "title": "Album",
                        "date": "2001",
                        "track_total": 10,
                        "disc_count": 1,
                        "formats": ["CD"],
                        "score": 0.9,
                    }
                ]

        daemon = AudioMetaDaemon(
            settings, interactive=False, discogs=None, musicbrainz=_MusicBrainzStub()
        )
        self.addCleanup(daemon.cache.close)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file1 = directory / "01 - First.flac"
            file2 = directory / "02 - Second.flac"
            file1.write_bytes(b"")
            file2.write_bytes(b"")

            def fake_tags(meta):
                if meta.path == file1:
                    return {
                        "title": "First Track",
                        "artist": "Example Artist",
                        "album": "Example Album",
                        "date": "2019",
                        "tracknumber": "1",
                        "performers": "Alice; Bob; Carol",
                    }
                return {
                    "title": "Second Track",
                    "artist": "Example Artist",
                    "album": "Example Album",
                    "date": "2019",
                    "tracknumber": "2",
                }

            with (
                patch.object(daemon, "_read_existing_tags", side_effect=fake_tags),
                patch("builtins.input", return_value="0"),
                patch("sys.stdout", new_callable=io.StringIO) as buf,
            ):
                selection = daemon._resolve_unmatched_directory(
                    directory,
                    sample_meta=None,
                    dir_track_count=2,
                    dir_year=None,
                    files=[file1, file2],
                )

            self.assertIsNone(selection)
            out = buf.getvalue()
            self.assertIn("Sample tracks:", out)
            self.assertIn("01 · First Track", out)
            self.assertIn("performers=Alice, Bob, …", out)
            self.assertIn("[01 - First.flac]", out)
            self.assertIn("02 · Second Track", out)


if __name__ == "__main__":
    unittest.main()
