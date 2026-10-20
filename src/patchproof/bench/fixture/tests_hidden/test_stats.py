import math

import pytest

from minilib.stats import clamp, mean, median, moving_average, percentile, variance


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5
    with pytest.raises(ValueError):
        mean([])


def test_median():
    assert median([5]) == 5
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    assert median([1, 2, 10, 20]) == 6
    with pytest.raises(ValueError):
        median([])


def test_variance():
    assert variance([2, 4, 4, 4, 5, 5, 7, 9], sample=False) == 4
    assert math.isclose(variance([2, 4, 4, 4, 5, 5, 7, 9]), 32 / 7)
    assert variance([1, 3]) == 2
    assert variance([5], sample=False) == 0
    with pytest.raises(ValueError):
        variance([5])


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 0) == 1
    assert percentile([1, 2, 3, 4, 5], 100) == 5
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert math.isclose(percentile([10, 20, 30, 40], 25), 17.5)
    assert percentile([7], 30) == 7
    with pytest.raises(ValueError):
        percentile([1], 101)
    with pytest.raises(ValueError):
        percentile([1], -1)
    with pytest.raises(ValueError):
        percentile([], 50)


def test_moving_average():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
    assert moving_average([1, 2, 3], 3) == [2]
    assert moving_average([1, 2], 3) == []
    assert moving_average([4, 8], 1) == [4, 8]
    with pytest.raises(ValueError):
        moving_average([1], 0)


def test_clamp():
    assert clamp(5, 1, 10) == 5
    assert clamp(-1, 1, 10) == 1
    assert clamp(11, 1, 10) == 10
    assert clamp(1, 1, 10) == 1
    assert clamp(10, 1, 10) == 10
    assert clamp(3, 3, 3) == 3
    with pytest.raises(ValueError):
        clamp(1, 5, 2)
