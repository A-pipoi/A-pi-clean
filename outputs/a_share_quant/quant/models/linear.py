from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LinearFactorModel:
    """Small ridge baseline; fitting must be called on a validation-produced train fold."""

    feature_names: tuple[str, ...]
    l2: float = 1.0
    intercept: float = 0.0
    weights: tuple[float, ...] | None = None

    def fit(self, rows: list[dict[str, float]], targets: list[float]) -> LinearFactorModel:
        if len(rows) != len(targets) or not rows:
            raise ValueError("rows and targets must be non-empty and aligned")
        if self.l2 < 0:
            raise ValueError("l2 must be non-negative")
        design = [[1.0] + [float(row.get(name, 0.0)) for name in self.feature_names] for row in rows]
        dimension = len(self.feature_names) + 1
        gram = [[0.0] * dimension for _ in range(dimension)]
        rhs = [0.0] * dimension
        for vector, target in zip(design, targets):
            for left in range(dimension):
                rhs[left] += vector[left] * target
                for right in range(dimension):
                    gram[left][right] += vector[left] * vector[right]
        for index in range(1, dimension):
            gram[index][index] += self.l2
        solution = _solve(gram, rhs)
        self.intercept = solution[0]
        self.weights = tuple(solution[1:])
        return self

    def predict(self, row: dict[str, float]) -> float:
        if self.weights is None:
            raise RuntimeError("model is not fitted")
        return self.intercept + sum(weight * float(row.get(name, 0.0)) for name, weight in zip(self.feature_names, self.weights))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    size = len(vector)
    for pivot in range(size):
        pivot_row = max(range(pivot, size), key=lambda row: abs(augmented[row][pivot]))
        if abs(augmented[pivot_row][pivot]) < 1e-12:
            raise ValueError("singular factor matrix")
        augmented[pivot], augmented[pivot_row] = augmented[pivot_row], augmented[pivot]
        divisor = augmented[pivot][pivot]
        augmented[pivot] = [value / divisor for value in augmented[pivot]]
        for row in range(size):
            if row == pivot:
                continue
            multiplier = augmented[row][pivot]
            augmented[row] = [
                left - multiplier * right for left, right in zip(augmented[row], augmented[pivot])
            ]
    return [augmented[index][-1] for index in range(size)]
