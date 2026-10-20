"""Descriptive statistics on lists of numbers."""


def mean(values):
    if not values:
        raise ValueError("mean of empty list")
    return sum(values) / len(values)


def median(values):
    if not values:
        raise ValueError("median of empty list")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def variance(values, sample=True):
    """Sample variance by default (divides by n - 1); population variance otherwise."""
    n = len(values)
    if n < 2 and sample:
        raise ValueError("sample variance needs at least two values")
    m = mean(values)
    squares = sum((v - m) ** 2 for v in values)
    return squares / (n - 1) if sample else squares / n


def percentile(values, p):
    """Linear-interpolation percentile, p in [0, 100]."""
    if not values:
        raise ValueError("percentile of empty list")
    if p < 0 or p > 100:
        raise ValueError("p must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def moving_average(values, window):
    """Averages of each full window of consecutive values."""
    if window <= 0:
        raise ValueError("window must be positive")
    result = []
    for i in range(len(values) - window + 1):
        chunk = values[i : i + window]
        result.append(sum(chunk) / window)
    return result


def clamp(x, lo, hi):
    if lo > hi:
        raise ValueError("lo must not exceed hi")
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
