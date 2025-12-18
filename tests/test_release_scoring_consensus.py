import unittest

from audio_meta.release_scoring import _dominant_value_consensus


class TestDominantValueConsensus(unittest.TestCase):
    def test_none_when_insufficient_consensus(self) -> None:
        values = ["A", "B", "A"]  # 2/3 = 0.66 < 0.7
        self.assertIsNone(_dominant_value_consensus(values, min_count=2, min_ratio=0.7))

    def test_value_when_sufficient_consensus(self) -> None:
        values = ["A", "A", "B", "A"]  # 3/4 = 0.75 >= 0.7
        self.assertEqual(
            _dominant_value_consensus(values, min_count=2, min_ratio=0.7), "A"
        )

    def test_single_value_returns_value(self) -> None:
        self.assertEqual(
            _dominant_value_consensus(["A"], min_count=2, min_ratio=0.7), "A"
        )


if __name__ == "__main__":
    unittest.main()
