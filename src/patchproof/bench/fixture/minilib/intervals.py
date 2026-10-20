"""Closed integer intervals represented as (start, end) tuples with start <= end."""


def overlaps(a, b):
    """Return True if closed intervals a and b share at least one point."""
    return a[0] <= b[1] and b[0] <= a[1]


def contains(interval, x):
    """Return True if x lies inside the closed interval."""
    start, end = interval
    return start <= x <= end


def length(interval):
    """Number of integer points in the closed interval."""
    start, end = interval
    return end - start + 1


def merge(intervals):
    """Merge overlapping or touching intervals and return them sorted."""
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1] + 1:
            last_start, last_end = result[-1]
            result[-1] = (last_start, max(last_end, end))
        else:
            result.append((start, end))
    return result


def total_length(intervals):
    """Number of integer points covered by the union of the intervals."""
    covered = 0
    for interval in merge(intervals):
        covered += length(interval)
    return covered


def gaps(intervals, lo, hi):
    """Return the uncovered closed intervals inside [lo, hi]."""
    result = []
    cursor = lo
    for start, end in merge(intervals):
        if end < lo or start > hi:
            continue
        if start > cursor:
            result.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= hi:
        result.append((cursor, hi))
    return result
