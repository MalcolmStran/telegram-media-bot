import time
from collections import deque, defaultdict
from config import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW

class RateLimiter:
    def __init__(self):
        # user_id -> deque of timestamps
        self._hits: dict[int, deque[float]] = defaultdict(lambda: deque())

    def allow(self, user_id: int) -> bool:
        now = time.time()
        dq = self._hits[user_id]
        # prune
        while dq and now - dq[0] > RATE_LIMIT_WINDOW:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_REQUESTS:
            return False
        dq.append(now)
        return True

rate_limiter = RateLimiter()
