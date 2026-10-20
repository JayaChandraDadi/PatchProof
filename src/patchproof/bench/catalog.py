"""Correct feature additions used to build benchmark PRs. Each one is appended to a module.

Every PR in the benchmark adds one of these, so a reviewer cannot find the seeded bug just
by looking for the only changed line.
"""

FEATURES = [
    {
        "module": "intervals",
        "title": "Add intersection() for closed intervals",
        "code": '''

def intersection(a, b):
    """The overlapping part of two closed intervals, or None if they do not overlap."""
    if not overlaps(a, b):
        return None
    return (max(a[0], b[0]), min(a[1], b[1]))
''',
    },
    {
        "module": "intervals",
        "title": "Add shift() to move a list of intervals",
        "code": '''

def shift(intervals, offset):
    """Move every interval by offset points."""
    return [(start + offset, end + offset) for start, end in intervals]
''',
    },
    {
        "module": "textutil",
        "title": "Add title_case() helper",
        "code": '''

def title_case(text):
    """Capitalise the first letter of every word and lowercase the rest."""
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split())
''',
    },
    {
        "module": "textutil",
        "title": "Add initials() helper",
        "code": '''

def initials(name):
    """Uppercase initials of each word, e.g. 'ada lovelace' -> 'AL'."""
    return "".join(word[0].upper() for word in name.split() if word)
''',
    },
    {
        "module": "stats",
        "title": "Add mode() to stats",
        "code": '''

def mode(values):
    """Most frequent value; the smallest one wins ties."""
    if not values:
        raise ValueError("mode of empty list")
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    return min(v for v, c in counts.items() if c == best)
''',
    },
    {
        "module": "stats",
        "title": "Add value_range() to stats",
        "code": '''

def value_range(values):
    """Difference between the largest and smallest value."""
    if not values:
        raise ValueError("range of empty list")
    return max(values) - min(values)
''',
    },
    {
        "module": "cart",
        "title": "Add empty_cart_like() for reusing a discount",
        "code": '''

def empty_cart_like(cart):
    """A new cart with the same discount and no items."""
    fresh = Cart()
    fresh.discount_percent = cart.discount_percent
    return fresh
''',
    },
    {
        "module": "cart",
        "title": "Add line_totals() for receipts",
        "code": '''

def line_totals(cart):
    """Per-SKU totals in cents, sorted by SKU, before any discount."""
    return [(sku, cart.prices[sku] * qty) for sku, qty in sorted(cart.items.items())]
''',
    },
    {
        "module": "search",
        "title": "Add contains_sorted() helper",
        "code": '''

def contains_sorted(items, target):
    """True if target is in the sorted list."""
    i = lower_bound(items, target)
    return i < len(items) and items[i] == target
''',
    },
    {
        "module": "search",
        "title": "Add bottom_k() next to top_k()",
        "code": '''

def bottom_k(values, k):
    """The k smallest values, smallest first."""
    if k <= 0:
        return []
    return sorted(values)[:k]
''',
    },
    {
        "module": "ratelimit",
        "title": "Add a helper that builds a per-second limiter",
        "code": '''

def per_second(rate, clock):
    """A bucket that allows `rate` requests per second with a burst of the same size."""
    return TokenBucket(rate, rate, clock)
''',
    },
    {
        "module": "ratelimit",
        "title": "Add available() to read the token count without spending",
        "code": '''

def available(bucket):
    """Tokens available right now, after refilling, without taking any."""
    bucket._refill()
    return bucket.tokens
''',
    },
    {
        "module": "pagination",
        "title": "Add has_previous() next to has_next()",
        "code": '''

def has_previous(page):
    return page > 1
''',
    },
    {
        "module": "pagination",
        "title": "Add page_of() to find the page holding an item index",
        "code": '''

def page_of(index, per_page):
    """1-based page number containing the 0-based item index."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return index // per_page + 1
''',
    },
]
