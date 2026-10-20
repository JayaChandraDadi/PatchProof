from minilib.search import binary_search, count_in_range, lower_bound, top_k, upper_bound

A = [1, 3, 5, 7, 9]
D = [1, 2, 2, 2, 5]


def test_binary_search():
    for i, v in enumerate(A):
        assert binary_search(A, v) == i
    for v in (0, 2, 4, 8, 10):
        assert binary_search(A, v) == -1
    assert binary_search([], 1) == -1
    assert binary_search([4], 4) == 0


def test_lower_bound():
    assert lower_bound(D, 2) == 1
    assert lower_bound(D, 0) == 0
    assert lower_bound(D, 3) == 4
    assert lower_bound(D, 6) == 5
    assert lower_bound([], 1) == 0


def test_upper_bound():
    assert upper_bound(D, 2) == 4
    assert upper_bound(D, 0) == 0
    assert upper_bound(D, 5) == 5
    assert upper_bound(D, 1) == 1


def test_count_in_range():
    assert count_in_range(D, 2, 2) == 3
    assert count_in_range(D, 1, 5) == 5
    assert count_in_range(D, 3, 4) == 0
    assert count_in_range(D, 5, 1) == 0
    assert count_in_range(A, 2, 8) == 3


def test_top_k():
    assert top_k([3, 1, 4, 1, 5], 2) == [5, 4]
    assert top_k([3, 1], 5) == [3, 1]
    assert top_k([3, 1], 0) == []
    assert top_k([2, 9, 4], 1) == [9]
