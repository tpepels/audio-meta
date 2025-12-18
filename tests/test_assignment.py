import unittest

from audio_meta.assignment import best_assignment_max_score, hungarian_min_cost


class TestAssignment(unittest.TestCase):
    def test_hungarian_min_cost_simple(self) -> None:
        cost = [
            [1.0, 10.0],
            [10.0, 1.0],
        ]
        self.assertEqual(hungarian_min_cost(cost), [0, 1])

    def test_best_assignment_max_score_identity(self) -> None:
        scores = [
            [1.0, 0.1],
            [0.2, 1.0],
        ]
        self.assertEqual(best_assignment_max_score(scores, dummy_score=0.0), [0, 1])

    def test_best_assignment_rectangular_with_unassigned(self) -> None:
        scores = [
            [0.9],
            [0.1],
        ]
        self.assertEqual(best_assignment_max_score(scores, dummy_score=0.0), [0, None])


if __name__ == "__main__":
    unittest.main()

