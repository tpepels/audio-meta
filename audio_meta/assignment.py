from __future__ import annotations

from typing import List, Optional


def hungarian_min_cost(cost: List[List[float]]) -> List[Optional[int]]:
    """
    Solve the rectangular assignment problem (minimize total cost).

    Returns a list `assignment` where assignment[row] = col (or None if unassigned).
    The algorithm pads to square internally and runs in O(n^3).
    """
    if not cost:
        return []
    n_rows = len(cost)
    n_cols = max(len(row) for row in cost)
    n = max(n_rows, n_cols)

    max_cost = 0.0
    for row in cost:
        for val in row:
            if val > max_cost:
                max_cost = val
    pad_value = max_cost + 1.0

    a: List[List[float]] = []
    for r in range(n):
        row: List[float] = []
        if r < n_rows:
            src = cost[r]
            for c in range(n):
                if c < len(src):
                    row.append(float(src[c]))
                else:
                    row.append(pad_value)
        else:
            row = [pad_value] * n
        a.append(row)

    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(0, n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = [None] * n_rows
    for j in range(1, n + 1):
        i = p[j]
        if 1 <= i <= n_rows and 1 <= j <= n_cols:
            assignment[i - 1] = j - 1
    return assignment


def best_assignment_max_score(
    score: List[List[float]],
    dummy_score: float,
) -> List[Optional[int]]:
    if not score:
        return []
    max_score = max((val for row in score for val in row), default=0.0)
    n_rows = len(score)
    n_cols = max(len(row) for row in score)
    n = max(n_rows, n_cols)

    cost: List[List[float]] = []
    for r in range(n_rows):
        row_scores = score[r]
        row_cost: List[float] = []
        for c in range(n):
            if c < len(row_scores):
                row_cost.append(max_score - float(row_scores[c]))
            else:
                row_cost.append(max_score - float(dummy_score))
        cost.append(row_cost)
    for _ in range(n - n_rows):
        cost.append([max_score - float(dummy_score)] * n)

    raw = hungarian_min_cost(cost)
    cleaned: List[Optional[int]] = []
    for r, c in enumerate(raw):
        if c is None or c >= n_cols:
            cleaned.append(None)
        else:
            cleaned.append(c)
    return cleaned

