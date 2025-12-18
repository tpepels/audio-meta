import tempfile
import unittest
from pathlib import Path

from audio_meta.daemon.prompt_preview import build_prompt_track_preview_lines
from audio_meta.models import TrackMetadata


class TestPromptPreviewRobustness(unittest.TestCase):
    def test_preview_does_not_crash_on_tag_reader_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            file_path = directory / "01 - Track.flac"
            file_path.write_bytes(b"")

            def explode(_meta: TrackMetadata):
                raise OSError("unreadable")

            lines = build_prompt_track_preview_lines(
                directory,
                files=[file_path],
                include_extensions=[".flac"],
                limit=1,
                read_existing_tags=explode,
                apply_tag_hints=lambda _m, _t: None,
            )
            self.assertEqual(len(lines), 1)
            self.assertIn("Track", lines[0])

    def test_preview_does_not_crash_on_apply_tag_hints_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            file_path = directory / "01 - Track.flac"
            file_path.write_bytes(b"")

            def apply_explode(_meta: TrackMetadata, _tags):
                raise RuntimeError("boom")

            lines = build_prompt_track_preview_lines(
                directory,
                files=[file_path],
                include_extensions=[".flac"],
                limit=1,
                read_existing_tags=lambda _m: {"title": "X"},
                apply_tag_hints=apply_explode,
            )
            self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()

