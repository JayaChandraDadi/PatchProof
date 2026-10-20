"""A token-bucket rate limiter with an injectable clock."""


class TokenBucket:
    def __init__(self, capacity, refill_per_second, clock):
        if capacity <= 0 or refill_per_second < 0:
            raise ValueError("capacity must be positive and refill non-negative")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        self.tokens = capacity
        self.updated = clock()

    def _refill(self):
        now = self.clock()
        elapsed = now - self.updated
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            self.updated = now

    def allow(self, cost=1):
        """Take cost tokens if available and return True; otherwise return False."""
        if cost > self.capacity:
            return False
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def wait_time(self, cost=1):
        """Seconds until cost tokens will be available (0 if available now)."""
        self._refill()
        missing = cost - self.tokens
        if missing <= 0:
            return 0.0
        if self.refill_per_second == 0:
            return float("inf")
        return missing / self.refill_per_second
