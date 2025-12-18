import unittest
from pathlib import Path
from types import SimpleNamespace

from audio_meta.services import AudioMetaServices


class TestAudioMetaServicesHelpers(unittest.TestCase):
    def test_release_key_and_split(self) -> None:
        daemon = SimpleNamespace(
            _release_key=lambda provider, rid: f"{provider}:{rid}",
            _split_release_key=lambda key: tuple(key.split(":", 1)),
            _display_path=lambda p: str(p),
        )
        services = AudioMetaServices(daemon)
        self.assertEqual(services.release_key("musicbrainz", "x"), "musicbrainz:x")
        self.assertEqual(services.split_release_key("discogs:123"), ("discogs", "123"))
        self.assertEqual(services.display_path(Path("/music/a")), "/music/a")


if __name__ == "__main__":
    unittest.main()
