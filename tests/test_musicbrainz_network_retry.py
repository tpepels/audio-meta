import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_meta.config import ProviderSettings
from audio_meta.models import TrackMetadata
from audio_meta.providers.musicbrainz import MusicBrainzClient


class TestMusicBrainzNetworkRetry(unittest.TestCase):
    def test_retries_network_errors_once_and_returns_none(self) -> None:
        calls = {"count": 0}

        class _MBStub:
            class NetworkError(Exception):
                pass

            class ResponseError(Exception):
                pass

            @staticmethod
            def set_useragent(*_args, **_kwargs) -> None:
                return None

            @staticmethod
            def search_recordings(**_kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise _MBStub.NetworkError("dns")
                return {"recording-list": []}

        settings = ProviderSettings(
            acoustid_api_key="x",
            musicbrainz_useragent="x",
            network_retries=1,
            network_retry_backoff_seconds=0.0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "01.flac"
            path.write_bytes(b"")
            meta = TrackMetadata(path=path, duration_seconds=1)

            with (
                patch("audio_meta.providers.musicbrainz.musicbrainzngs", _MBStub),
                patch.object(MusicBrainzClient, "_fingerprint", return_value=(None, None)),
                patch.object(
                    MusicBrainzClient,
                    "_read_basic_tags",
                    return_value={"artist": "A", "title": "T", "album": "AL"},
                ),
                patch.object(MusicBrainzClient, "_lookup_by_guess", return_value=None),
            ):
                client = MusicBrainzClient(settings)
                result = client.enrich(meta)

        self.assertIsNone(result)
        self.assertEqual(calls["count"], 2)

    def test_persistent_network_errors_do_not_raise(self) -> None:
        calls = {"count": 0}

        class _MBStub:
            class NetworkError(Exception):
                pass

            class ResponseError(Exception):
                pass

            @staticmethod
            def set_useragent(*_args, **_kwargs) -> None:
                return None

            @staticmethod
            def search_recordings(**_kwargs):
                calls["count"] += 1
                raise _MBStub.NetworkError("dns")

        settings = ProviderSettings(
            acoustid_api_key="x",
            musicbrainz_useragent="x",
            network_retries=1,
            network_retry_backoff_seconds=0.0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "01.flac"
            path.write_bytes(b"")
            meta = TrackMetadata(path=path, duration_seconds=1)

            with (
                patch("audio_meta.providers.musicbrainz.musicbrainzngs", _MBStub),
                patch.object(MusicBrainzClient, "_fingerprint", return_value=(None, None)),
                patch.object(
                    MusicBrainzClient,
                    "_read_basic_tags",
                    return_value={"artist": "A", "title": "T", "album": "AL"},
                ),
                patch.object(MusicBrainzClient, "_lookup_by_guess", return_value=None),
            ):
                client = MusicBrainzClient(settings)
                result = client.enrich(meta)

        self.assertIsNone(result)
        self.assertEqual(calls["count"], 2)


if __name__ == "__main__":
    unittest.main()
