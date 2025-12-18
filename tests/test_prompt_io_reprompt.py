import unittest
from pathlib import Path

from audio_meta.config import DaemonSettings, LibrarySettings, OrganizerSettings, ProviderSettings, Settings
from audio_meta.daemon import AudioMetaDaemon
from audio_meta.daemon_types import ReleaseExample
from audio_meta.prompt_io import BufferPromptIO


class TestPromptIORetry(unittest.TestCase):
    def test_invalid_choice_then_out_of_range_reprompts(self) -> None:
        prompt_io = BufferPromptIO(inputs=["x", "999", "0"])
        settings = Settings(
            library=LibrarySettings(roots=[Path("/music")]),
            providers=ProviderSettings(acoustid_api_key="x", musicbrainz_useragent="x"),
            organizer=OrganizerSettings(enabled=False),
            daemon=DaemonSettings(
                prompt_show_urls=False,
                prompt_expand_mb_candidates=False,
                prompt_mb_search_limit=0,
                prompt_preview_tracks=0,
            ),
        )
        daemon = AudioMetaDaemon(settings, interactive=False, discogs=None, prompt_io=prompt_io)
        self.addCleanup(daemon.cache.close)

        release_id = "df26158f-1cba-45b9-b54c-1d2857a41d2b"
        key = f"musicbrainz:{release_id}"
        release_examples = {
            key: ReleaseExample(
                provider="musicbrainz",
                title="Album",
                artist="Artist",
                date="2001",
                track_total=6,
                disc_count=1,
                formats=["CD"],
            )
        }

        selection = daemon._resolve_release_interactively(
            Path("/music/Some/Folder"),
            [(key, 1.10)],
            release_examples,
            sample_meta=None,
            dir_track_count=6,
            dir_year=None,
            discogs_details={},
            prompt_title="Confirm match",
        )

        self.assertIsNone(selection)
        out = "\n".join(prompt_io.outputs)
        self.assertIn("Invalid selection", out)
        self.assertIn("Selection out of range.", out)
        self.assertGreaterEqual(len(prompt_io.prompts), 3)


if __name__ == "__main__":
    unittest.main()

