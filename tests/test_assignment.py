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

    def test_best_assignment_prefers_dummy_when_scores_low(self) -> None:
        scores = [
            [0.1],
            [0.1],
        ]
        assignment = best_assignment_max_score(scores, dummy_score=0.62)
        self.assertEqual(len(assignment), 2)
        self.assertEqual(sum(1 for x in assignment if x is None), 1)
        self.assertEqual(sorted(x for x in assignment if x is not None), [0])

    def test_best_assignment_unique_even_with_ties(self) -> None:
        scores = [
            [0.9, 0.9],
            [0.9, 0.9],
        ]
        assignment = best_assignment_max_score(scores, dummy_score=0.0)
        self.assertEqual(sorted(assignment), [0, 1])

    def test_best_assignment_more_columns_than_rows(self) -> None:
        scores = [
            [0.9, 0.1, 0.2],
            [0.2, 0.8, 0.1],
        ]
        assignment = best_assignment_max_score(scores, dummy_score=0.0)
        self.assertEqual(len(assignment), 2)
        self.assertTrue(all(a is None or 0 <= a < 3 for a in assignment))
        self.assertEqual(len({a for a in assignment if a is not None}), 2)

    def test_best_assignment_single_row_many_columns(self) -> None:
        scores = [[0.1, 0.2, 0.9, 0.0]]
        assignment = best_assignment_max_score(scores, dummy_score=0.0)
        self.assertEqual(assignment, [2])


if __name__ == "__main__":
    unittest.main()
