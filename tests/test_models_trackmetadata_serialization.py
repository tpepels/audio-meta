import json
import unittest
from pathlib import Path

from audio_meta.models import TrackMetadata


class TestTrackMetadataSerialization(unittest.TestCase):
    def test_to_record_omits_fingerprint(self) -> None:
        meta = TrackMetadata(path=Path("/music/a.flac"), fingerprint="abc123")
        record = meta.to_record()
        self.assertEqual(record["path"], "/music/a.flac")
        self.assertEqual(record["fingerprint"], "<omitted>")
        self.assertIsNone(record["acoustid_id"])

    def test_to_record_is_json_serializable(self) -> None:
        meta = TrackMetadata(
            path=Path("/music/a.flac"),
            fingerprint="fp",
            title=b"Etude \xe2\x80\x93 Op. 10 No. 1",
            performers=["Martha Argerich", b"Claudio Abbado"],
            extra={
                b"RAW": b"\xff\xfe",
                Path("/tmp/key"): Path("/tmp/value"),
                "nested": {b"inner": [b"a", Path("/x/y")]},
            },
        )
        record = meta.to_record()

        json.dumps(record)
        self.assertIsInstance(record["title"], str)
        self.assertEqual(record["fingerprint"], "<omitted>")

        extra = record["extra"]
        self.assertIn("RAW", extra)
        self.assertEqual(extra["RAW"], "\ufffd\ufffd")
        self.assertIn("/tmp/key", extra)
        self.assertEqual(extra["/tmp/key"], "/tmp/value")

        nested = extra["nested"]
        self.assertEqual(nested["inner"][0], "a")
        self.assertEqual(nested["inner"][1], "/x/y")


if __name__ == "__main__":
    unittest.main()
