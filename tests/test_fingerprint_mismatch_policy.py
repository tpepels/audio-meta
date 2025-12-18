import unittest
from pathlib import Path

from audio_meta.config import (
    DaemonSettings,
    LibrarySettings,
    OrganizerSettings,
    ProviderSettings,
    Settings,
)
from audio_meta.daemon import AudioMetaDaemon
from audio_meta.models import TrackMetadata


class TestFingerprintMismatchPolicy(unittest.TestCase):
    def _daemon(self, threshold: float = 0.35) -> AudioMetaDaemon:
        settings = Settings(
            library=LibrarySettings(
                roots=[Path("/music")], include_extensions=[".mp3"], exclude_patterns=[]
            ),
            providers=ProviderSettings(
                acoustid_api_key="x", musicbrainz_useragent="test"
            ),
            organizer=OrganizerSettings(enabled=False),
            daemon=DaemonSettings(
                fingerprint_mismatch_threshold=threshold,
                fingerprint_mismatch_action="defer",
            ),
        )
        daemon = AudioMetaDaemon(settings, interactive=False)
        self.addCleanup(daemon.cache.close)
        return daemon

    def test_conflict_when_album_and_artist_both_mismatch(self) -> None:
        daemon = self._daemon(threshold=0.6)
        tags = {"album": "Lamenti", "album_artist": "Anne Sofie von Otter"}
        meta = TrackMetadata(
            path=Path("/music/x.mp3"), album="Nocturnes", album_artist="Murray Perahia"
        )
        self.assertTrue(daemon._fingerprint_conflicts_with_tags(tags, meta))

    def test_no_conflict_when_tags_missing(self) -> None:
        daemon = self._daemon()
        meta = TrackMetadata(
            path=Path("/music/x.mp3"), album="Album", album_artist="Artist"
        )
        self.assertFalse(daemon._fingerprint_conflicts_with_tags({}, meta))


if __name__ == "__main__":
    unittest.main()
