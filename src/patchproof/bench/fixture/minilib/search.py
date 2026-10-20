"""Searching sorted lists."""


def binary_search(items, target):
    """Index of target in the sorted list, or -1."""
    lo = 0
    hi = len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def lower_bound(items, target):
    """First index whose value is >= target (len(items) if none)."""
    lo = 0
    hi = len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def upper_bound(items, target):
    """First index whose value is > target (len(items) if none)."""
    lo = 0
    hi = len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        if items[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def count_in_range(items, lo, hi):
    """How many values v satisfy lo <= v <= hi in the sorted list."""
    if lo > hi:
        return 0
    return upper_bound(items, hi) - lower_bound(items, lo)


def top_k(values, k):
    """The k largest values, largest first."""
    if k <= 0:
        return []
    return sorted(values, reverse=True)[:k]
