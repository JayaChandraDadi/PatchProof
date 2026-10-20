import pytest

from minilib.cart import Cart


def make():
    c = Cart()
    c.add("pen", 150, 2)
    c.add("book", 1000)
    return c


def test_add_and_count():
    c = make()
    assert c.item_count() == 3
    c.add("pen", 150)
    assert c.items["pen"] == 3
    assert c.subtotal() == 1450
    c.add("free", 0)
    assert c.subtotal() == 1450
    with pytest.raises(ValueError):
        c.add("x", 100, 0)
    with pytest.raises(ValueError):
        c.add("x", -1)


def test_remove():
    c = make()
    c.remove("pen")
    assert c.items["pen"] == 1
    c.remove("pen")
    assert "pen" not in c.items
    c.remove("book", 1)
    assert c.items == {}
    with pytest.raises(ValueError):
        c.remove("book")
    c.add("cup", 300, 2)
    with pytest.raises(ValueError):
        c.remove("cup", 3)
    c.remove("cup", 2)
    assert c.item_count() == 0


def test_subtotal():
    assert make().subtotal() == 1300
    assert Cart().subtotal() == 0


def test_discount_and_total():
    c = make()
    assert c.total() == 1300
    c.apply_discount(10)
    assert c.total() == 1170
    assert c.total(tax_rate=0.1) == 1287
    c.apply_discount(100)
    assert c.total() == 0
    c.apply_discount(0)
    assert c.total() == 1300
    with pytest.raises(ValueError):
        c.apply_discount(101)
    with pytest.raises(ValueError):
        c.apply_discount(-5)


def test_rounding():
    c = Cart()
    c.add("a", 999)
    assert c.total(tax_rate=0.085) == 1084
    c2 = Cart()
    c2.add("b", 3)
    c2.apply_discount(50)
    assert c2.total() == 1
