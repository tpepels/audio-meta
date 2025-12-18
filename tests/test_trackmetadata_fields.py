import unittest
from pathlib import Path

from audio_meta.models import TrackMetadata


class TestTrackMetadataFields(unittest.TestCase):
    def test_migrates_common_extra_keys_to_typed_fields(self) -> None:
        meta = TrackMetadata(
            path=Path("/music/01.flac"),
            extra={
                "TRACKNUMBER": "03/12",
                "DISCNUMBER": 2,
                "TRACK_TOTAL": "10",
                "MATCH_SOURCE": "acoustid",
                "OTHER": "x",
            },
        )
        self.assertEqual(meta.track_number, 3)
        self.assertEqual(meta.disc_number, 2)
        self.assertEqual(meta.track_total, 10)
        self.assertEqual(meta.match_source, "acoustid")
        self.assertEqual(meta.extra.get("OTHER"), "x")
        self.assertNotIn("TRACKNUMBER", meta.extra)
        self.assertNotIn("DISCNUMBER", meta.extra)
        self.assertNotIn("TRACK_TOTAL", meta.extra)
        self.assertNotIn("MATCH_SOURCE", meta.extra)

    def test_to_record_includes_typed_fields(self) -> None:
        meta = TrackMetadata(
            path=Path("/music/01.flac"),
            track_number=1,
            disc_number=1,
            track_total=9,
            match_source="acoustid",
            extra={"X": "y"},
        )
        record = meta.to_record()
        self.assertEqual(record["track_number"], 1)
        self.assertEqual(record["disc_number"], 1)
        self.assertEqual(record["track_total"], 9)
        self.assertEqual(record["match_source"], "acoustid")
        self.assertEqual(record["extra"], {"X": "y"})


if __name__ == "__main__":
    unittest.main()

