"""A shopping cart that keeps money in integer cents."""


class Cart:
    def __init__(self):
        self.items = {}
        self.prices = {}
        self.discount_percent = 0

    def add(self, sku, price_cents, qty=1):
        if qty <= 0:
            raise ValueError("qty must be positive")
        if price_cents < 0:
            raise ValueError("price must not be negative")
        self.prices[sku] = price_cents
        self.items[sku] = self.items.get(sku, 0) + qty

    def remove(self, sku, qty=1):
        """Remove qty units; the line disappears when its quantity reaches zero."""
        current = self.items.get(sku, 0)
        if qty > current:
            raise ValueError("cannot remove more than is in the cart")
        if current - qty == 0:
            del self.items[sku]
        else:
            self.items[sku] = current - qty

    def item_count(self):
        return sum(self.items.values())

    def subtotal(self):
        total = 0
        for sku, qty in self.items.items():
            total += self.prices[sku] * qty
        return total

    def apply_discount(self, percent):
        if percent < 0 or percent > 100:
            raise ValueError("percent must be between 0 and 100")
        self.discount_percent = percent

    def total(self, tax_rate=0.0):
        """Discounted subtotal plus tax, rounded half up to whole cents."""
        discounted = self.subtotal() * (100 - self.discount_percent) // 100
        tax = discounted * tax_rate
        return int(discounted + tax + 0.5)
