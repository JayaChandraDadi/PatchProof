import math

import pytest

from minilib.ratelimit import TokenBucket


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_burst_then_block():
    c = Clock()
    b = TokenBucket(3, 1, c)
    assert b.allow() and b.allow() and b.allow()
    assert not b.allow()


def test_refill():
    c = Clock()
    b = TokenBucket(2, 2, c)
    assert b.allow(2)
    assert not b.allow()
    c.t += 0.5
    assert b.allow()
    assert not b.allow()
    c.t += 10
    assert b.tokens <= 2
    assert b.allow(2)
    assert not b.allow()


def test_cost():
    c = Clock()
    b = TokenBucket(5, 1, c)
    assert not b.allow(6)
    assert b.tokens == 5
    assert b.allow(5)
    assert b.tokens == 0


def test_exact_tokens():
    c = Clock()
    b = TokenBucket(4, 1, c)
    assert b.allow(3)
    assert b.allow(1)
    assert b.tokens == 0


def test_wait_time():
    c = Clock()
    b = TokenBucket(4, 2, c)
    assert b.wait_time() == 0
    assert b.allow(4)
    assert b.wait_time(1) == 0.5
    assert b.wait_time(3) == 1.5
    c.t += 0.5
    assert b.wait_time(1) == 0
    z = TokenBucket(1, 0, c)
    assert z.allow()
    assert math.isinf(z.wait_time())


def test_invalid():
    with pytest.raises(ValueError):
        TokenBucket(0, 1, Clock())
    with pytest.raises(ValueError):
        TokenBucket(1, -1, Clock())
    assert TokenBucket(1, 0, Clock()).allow()
