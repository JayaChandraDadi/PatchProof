import pytest

from minilib.pagination import has_next, page_count, page_slice, page_window


def test_page_count():
    assert page_count(0, 10) == 0
    assert page_count(1, 10) == 1
    assert page_count(10, 10) == 1
    assert page_count(11, 10) == 2
    with pytest.raises(ValueError):
        page_count(5, 0)


def test_page_slice():
    items = list(range(10))
    assert page_slice(items, 1, 4) == [0, 1, 2, 3]
    assert page_slice(items, 2, 4) == [4, 5, 6, 7]
    assert page_slice(items, 3, 4) == [8, 9]
    assert page_slice(items, 4, 4) == []
    with pytest.raises(ValueError):
        page_slice(items, 0, 4)


def test_page_window():
    assert page_window(1, 10, 5) == [1, 2, 3, 4, 5]
    assert page_window(5, 10, 5) == [3, 4, 5, 6, 7]
    assert page_window(10, 10, 5) == [6, 7, 8, 9, 10]
    assert page_window(9, 10, 4) == [7, 8, 9, 10]
    assert page_window(2, 3, 5) == [1, 2, 3]
    assert page_window(1, 0, 5) == []
    assert page_window(4, 10, 1) == [4]


def test_has_next():
    assert has_next(1, 11, 10)
    assert not has_next(2, 11, 10)
    assert not has_next(1, 10, 10)
