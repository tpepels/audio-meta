import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from audio_meta.cache import MetadataCache
from audio_meta.pipeline.contexts import TrackSkipContext
from audio_meta.pipeline.plugins.track_skip import DefaultTrackSkipPolicyPlugin


class TestTrackSkipForcePrompt(unittest.TestCase):
    def test_force_prompt_disables_processed_file_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            audio = tmp / "01.mp3"
            audio.write_bytes(b"\x00\x01\x02")
            stat = audio.stat()

            cache = MetadataCache(tmp / "cache.sqlite3")
            self.addCleanup(cache.close)
            cache.set_processed_file(audio, stat.st_mtime_ns, stat.st_size, True)

            daemon = SimpleNamespace(
                dry_run_recorder=None,
                services=SimpleNamespace(safe_stat=lambda p: Path(p).stat()),
                cache=cache,
                organizer=SimpleNamespace(enabled=False),
            )

            plugin = DefaultTrackSkipPolicyPlugin()

            ctx_skip = TrackSkipContext(
                daemon=daemon,
                directory=tmp,
                file_path=audio,
                directory_ctx=SimpleNamespace(force_prompt=False),
            )
            decision = plugin.should_skip(ctx_skip)
            assert decision is not None
            self.assertTrue(decision.should_skip)
            self.assertEqual(decision.reason, "already_processed_unchanged")

            ctx_force = TrackSkipContext(
                daemon=daemon,
                directory=tmp,
                file_path=audio,
                directory_ctx=SimpleNamespace(force_prompt=True),
            )
            decision = plugin.should_skip(ctx_force)
            assert decision is not None
            self.assertFalse(decision.should_skip)


if __name__ == "__main__":
    unittest.main()
