"""The PR's own CI: happy-path tests only, like many real projects."""

from minilib import cart, intervals, pagination, search, stats, textutil
from minilib.ratelimit import TokenBucket


def test_intervals_basic():
    assert intervals.merge([(5, 8), (1, 3)]) == [(1, 3), (5, 8)]
    assert intervals.overlaps((1, 5), (3, 9))


def test_text_basic():
    assert textutil.slugify("Hello World") == "hello-world"
    assert textutil.count_words("a b c") == 3


def test_stats_basic():
    assert stats.mean([2, 4]) == 3
    assert stats.median([3, 1, 2]) == 2


def test_cart_basic():
    c = cart.Cart()
    c.add("pen", 150, 2)
    assert c.subtotal() == 300


def test_search_basic():
    assert search.binary_search([1, 3, 5, 7, 9], 7) == 3


def test_ratelimit_basic():
    t = [0.0]
    b = TokenBucket(5, 1, lambda: t[0])
    assert b.allow()


def test_pagination_basic():
    assert pagination.page_slice(list(range(10)), 1, 4) == [0, 1, 2, 3]
