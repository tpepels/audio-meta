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
from audio_meta.prompt_io import BufferPromptIO


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
        prompt_io = BufferPromptIO(inputs=["0"])
        daemon = AudioMetaDaemon(
            settings, interactive=False, discogs=None, prompt_io=prompt_io
        )
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
        out = "\n".join(prompt_io.outputs)
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

        prompt_io = BufferPromptIO(inputs=["0"])
        daemon = AudioMetaDaemon(
            settings,
            interactive=False,
            discogs=None,
            musicbrainz=_MusicBrainzStub(),
            prompt_io=prompt_io,
        )
        self.addCleanup(daemon.cache.close)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file_path = directory / "01.flac"
            file_path.write_bytes(b"")

            selection = daemon._resolve_unmatched_directory(
                directory,
                sample_meta=None,
                dir_track_count=2,
                dir_year=None,
                files=[file_path],
            )

        self.assertIsNone(selection)
        out = "\n".join(prompt_io.outputs)
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

        prompt_io = BufferPromptIO(inputs=["0"])
        daemon = AudioMetaDaemon(
            settings,
            interactive=False,
            discogs=None,
            musicbrainz=_MusicBrainzStub(),
            prompt_io=prompt_io,
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

            with patch.object(daemon, "_read_existing_tags", side_effect=fake_tags):
                selection = daemon._resolve_unmatched_directory(
                    directory,
                    sample_meta=None,
                    dir_track_count=2,
                    dir_year=None,
                    files=[file1, file2],
                )

            self.assertIsNone(selection)
            out = "\n".join(prompt_io.outputs)
            self.assertIn("Sample tracks:", out)
            self.assertIn("01 · First Track", out)
            self.assertIn("performers=Alice, Bob, …", out)
            self.assertIn("[01 - First.flac]", out)
        self.assertIn("02 · Second Track", out)

    def test_prompt_option_includes_assignment_diagnostics(self) -> None:
        from audio_meta.providers.musicbrainz import ReleaseData, ReleaseTrack

        settings = Settings(
            library=LibrarySettings(roots=[Path("/music")], include_extensions=[".flac"]),
            providers=ProviderSettings(acoustid_api_key="x", musicbrainz_useragent="x"),
            organizer=OrganizerSettings(enabled=False),
            daemon=DaemonSettings(
                prompt_show_urls=False,
                prompt_expand_mb_candidates=False,
                prompt_mb_search_limit=0,
                prompt_preview_tracks=0,
            ),
        )

        release = ReleaseData("rid", "Album", "Artist", "2001")
        release.add_track(
            ReleaseTrack(
                recording_id="t1",
                disc_number=1,
                number=1,
                title="Alpha",
                duration_seconds=None,
            )
        )
        release.add_track(
            ReleaseTrack(
                recording_id="t2",
                disc_number=1,
                number=2,
                title="Beta",
                duration_seconds=None,
            )
        )

        class _MBStub:
            class _Tracker:
                def __init__(self, releases):
                    self.releases = releases

            def __init__(self, releases):
                self.release_tracker = _MBStub._Tracker(releases=releases)

            def _fetch_release_tracks(self, release_id: str):
                return self.release_tracker.releases.get(release_id)

        prompt_io = BufferPromptIO(inputs=["0"])
        daemon = AudioMetaDaemon(
            settings,
            interactive=False,
            discogs=None,
            musicbrainz=_MBStub({"rid": release}),
            prompt_io=prompt_io,
        )
        self.addCleanup(daemon.cache.close)

        key = "musicbrainz:rid"
        release_examples = {
            key: ReleaseExample(
                provider="musicbrainz",
                title="Album",
                artist="Artist",
                date="2001",
                track_total=2,
                disc_count=1,
                formats=["Digital Media"],
            )
        }

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file1 = directory / "01 - Alpha.flac"
            file2 = directory / "02 - Beta.flac"
            file1.write_bytes(b"")
            file2.write_bytes(b"")

            selection = daemon._resolve_release_interactively(
                directory,
                [(key, 1.0)],
                release_examples,
                sample_meta=None,
                dir_track_count=2,
                dir_year=2001,
                discogs_details={},
                files=[file1, file2],
            )

            self.assertIsNone(selection)
            out = "\n".join(prompt_io.outputs)
            self.assertIn("cov 100%", out)
            self.assertIn("avg 1.00", out)
            self.assertIn("2/2", out)


if __name__ == "__main__":
    unittest.main()
