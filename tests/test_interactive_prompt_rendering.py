import io
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

        with (
            patch("builtins.input", return_value="0"),
            patch("sys.stdout", new_callable=io.StringIO) as buf,
        ):
            selection = daemon._resolve_unmatched_directory(
                Path("/music/Some/Folder"),
                sample_meta=None,
                dir_track_count=2,
                dir_year=None,
                files=[Path("/music/Some/Folder/01.flac")],
            )

        self.assertIsNone(selection)
        out = buf.getvalue()
        self.assertIn("No automatic metadata match for", out)
        self.assertIn(
            "https://musicbrainz.org/release/df26158f-1cba-45b9-b54c-1d2857a41d2b", out
        )
        self.assertNotIn("dg:<release-id>", out)


if __name__ == "__main__":
    unittest.main()
