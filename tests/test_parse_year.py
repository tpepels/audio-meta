import unittest

from audio_meta.daemon import AudioMetaDaemon


class TestParseYear(unittest.TestCase):
    def test_parse_year_accepts_mutagen_id3_timestamp(self) -> None:
        from mutagen.id3 import ID3TimeStamp

        self.assertEqual(AudioMetaDaemon._parse_year(ID3TimeStamp("1998")), 1998)


if __name__ == "__main__":
    unittest.main()
