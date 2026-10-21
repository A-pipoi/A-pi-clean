from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    train_indices: tuple[int, ...]
    embargo_indices: tuple[int, ...]
    test_indices: tuple[int, ...]


class PurgedWalkForward:
    """Strictly ordered rolling validation with a post-training embargo."""

    def __init__(self, train_window: int, test_window: int, embargo: int) -> None:
        if min(train_window, test_window) <= 0 or embargo < 0:
            raise ValueError("windows must be positive and embargo cannot be negative")
        self.train_window = train_window
        self.test_window = test_window
        self.embargo = embargo

    def split(self, observations: Sequence[object]) -> list[Fold]:
        n = len(observations)
        folds: list[Fold] = []
        train_end = self.train_window
        while train_end + self.embargo + self.test_window <= n:
            train = tuple(range(train_end - self.train_window, train_end))
            embargo = tuple(range(train_end, train_end + self.embargo))
            test_start = train_end + self.embargo
            test = tuple(range(test_start, test_start + self.test_window))
            folds.append(Fold(train, embargo, test))
            train_end += self.test_window
        if not folds:
            raise ValueError("not enough observations for one purged walk-forward fold")
        return folds

    def validate_no_overlap(self, folds: list[Fold]) -> None:
        for fold in folds:
            if set(fold.train_indices) & set(fold.embargo_indices + fold.test_indices):
                raise AssertionError("train data overlaps embargo or test data")
            if set(fold.embargo_indices) & set(fold.test_indices):
                raise AssertionError("embargo overlaps test data")
