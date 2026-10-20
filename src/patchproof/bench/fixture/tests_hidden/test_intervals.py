from minilib.intervals import contains, gaps, length, merge, overlaps, total_length


def test_overlaps():
    assert overlaps((1, 5), (5, 9))
    assert overlaps((5, 9), (1, 5))
    assert not overlaps((1, 4), (5, 9))
    assert not overlaps((6, 9), (1, 5))
    assert overlaps((1, 10), (3, 4))
    assert overlaps((3, 4), (1, 10))


def test_contains():
    assert contains((2, 5), 2)
    assert contains((2, 5), 5)
    assert contains((2, 5), 3)
    assert not contains((2, 5), 1)
    assert not contains((2, 5), 6)


def test_length():
    assert length((3, 3)) == 1
    assert length((1, 10)) == 10


def test_merge():
    assert merge([]) == []
    assert merge([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]
    assert merge([(1, 3), (4, 6)]) == [(1, 6)]
    assert merge([(1, 3), (5, 6)]) == [(1, 3), (5, 6)]
    assert merge([(1, 10), (2, 3)]) == [(1, 10)]
    assert merge([(5, 8), (1, 3)]) == [(1, 3), (5, 8)]
    assert merge([(4, 6), (1, 2)]) == [(1, 2), (4, 6)]


def test_total_length():
    assert total_length([]) == 0
    assert total_length([(1, 3)]) == 3
    assert total_length([(1, 3), (2, 5)]) == 5
    assert total_length([(1, 3), (7, 8)]) == 5


def test_gaps():
    assert gaps([], 1, 5) == [(1, 5)]
    assert gaps([(1, 5)], 1, 5) == []
    assert gaps([(2, 3)], 1, 5) == [(1, 1), (4, 5)]
    assert gaps([(1, 2), (4, 5)], 1, 5) == [(3, 3)]
    assert gaps([(0, 2), (8, 20)], 1, 10) == [(3, 7)]
    assert gaps([(20, 30)], 1, 5) == [(1, 5)]
    assert gaps([(3, 3)], 3, 4) == [(4, 4)]
    assert gaps([(4, 4)], 3, 4) == [(3, 3)]
