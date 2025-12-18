import unittest
from contextlib import contextmanager
from pathlib import Path

from audio_meta.config import (
    ClassicalSettings,
    DaemonSettings,
    LibrarySettings,
    OrganizerSettings,
    ProviderSettings,
    Settings,
)
from audio_meta.daemon import AudioMetaDaemon
from audio_meta.models import TrackMetadata


class TestClassicalCreditsReview(unittest.TestCase):
    @contextmanager
    def _with_daemon(self) -> AudioMetaDaemon:
        daemon = self._daemon()
        try:
            yield daemon
        finally:
            daemon.cache.close()

    def _daemon(self) -> AudioMetaDaemon:
        settings = Settings(
            library=LibrarySettings(
                roots=[Path("/music")],
                include_extensions=[".flac"],
                exclude_patterns=[],
            ),
            providers=ProviderSettings(
                acoustid_api_key="x", musicbrainz_useragent="test"
            ),
            organizer=OrganizerSettings(enabled=False),
            classical=ClassicalSettings(
                genre_keywords=["classical"], min_duration_seconds=10
            ),
            daemon=DaemonSettings(
                classical_credits_min_tracks=3,
                classical_credits_min_coverage=0.6,
                classical_credits_min_consensus=0.7,
                classical_credits_action="defer",
            ),
        )
        return AudioMetaDaemon(settings, interactive=False)

    def test_daemon_is_closed(self) -> None:
        with self._with_daemon():
            pass

    def test_review_when_missing_performer_hints(self) -> None:
        with self._with_daemon() as daemon:
            metas = [
                TrackMetadata(
                    path=Path(f"/music/a{i}.flac"),
                    genre="Classical",
                    title="Symphony",
                    composer="Gustav Mahler",
                    album_artist="Gustav Mahler",
                    artist="Gustav Mahler",
                    duration_seconds=600,
                )
                for i in range(3)
            ]
            self.assertTrue(daemon._should_review_classical_credits(metas))
            stats = daemon._classical_credits_stats(metas)
            self.assertEqual(stats["classical_tracks"], 3)
            self.assertEqual(stats["hinted_tracks"], 0)
            self.assertEqual(stats["missing_hints"], 3)

    def test_no_review_when_performers_consistent(self) -> None:
        with self._with_daemon() as daemon:
            metas = [
                TrackMetadata(
                    path=Path(f"/music/a{i}.flac"),
                    genre="Classical",
                    title="Concerto",
                    composer="Maurice Ravel",
                    performers=["Martha Argerich", "Berliner Philharmoniker"],
                    conductor="Claudio Abbado",
                    duration_seconds=600,
                )
                for i in range(3)
            ]
            self.assertFalse(daemon._should_review_classical_credits(metas))

    def test_review_when_performers_inconsistent(self) -> None:
        with self._with_daemon() as daemon:
            metas = [
                TrackMetadata(
                    path=Path("/music/a1.flac"),
                    genre="Classical",
                    title="Etude",
                    composer="Frédéric Chopin",
                    performers=["Maurizio Pollini"],
                    duration_seconds=300,
                ),
                TrackMetadata(
                    path=Path("/music/a2.flac"),
                    genre="Classical",
                    title="Etude",
                    composer="Frédéric Chopin",
                    performers=["Murray Perahia"],
                    duration_seconds=300,
                ),
                TrackMetadata(
                    path=Path("/music/a3.flac"),
                    genre="Classical",
                    title="Etude",
                    composer="Frédéric Chopin",
                    performers=["Murray Perahia"],
                    duration_seconds=300,
                ),
            ]
            self.assertTrue(daemon._should_review_classical_credits(metas))


if __name__ == "__main__":
    unittest.main()
