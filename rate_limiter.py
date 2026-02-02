"""Async sliding window rate limiter for web search API calls."""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Tuple


@dataclass
class RateLimiterStats:
    """Statistics for rate limiter."""

    total_requests: int = 0
    total_searches: int = 0
    window_requests: int = 0
    current_rpm: float = 0.0
    avg_searches_per_request: float = 0.0


@dataclass
class AsyncSlidingWindowRateLimiter:
    """
    Sliding window rate limiter for web search requests.

    Tracks web_search_requests from API responses and ensures
    we stay within the RPM limit.
    """

    max_rpm: int = 30
    window_seconds: float = 60.0
    _requests: Deque[Tuple[float, int]] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _total_requests: int = 0
    _total_searches: int = 0

    async def acquire(self, estimated_searches: int = 5) -> None:
        """
        Wait until we can make a request with estimated searches.

        Args:
            estimated_searches: Expected number of web searches for this request.
        """
        async with self._lock:
            while True:
                self._cleanup_old_requests()

                current_searches = sum(s for _, s in self._requests)

                # Check if we have room for estimated searches
                if current_searches + estimated_searches <= self.max_rpm:
                    break

                # Calculate wait time
                if self._requests:
                    oldest_time, _ = self._requests[0]
                    wait_time = oldest_time + self.window_seconds - time.monotonic()
                    if wait_time > 0:
                        await asyncio.sleep(wait_time + 0.1)
                else:
                    await asyncio.sleep(0.1)

    async def consume(self, actual_searches: int) -> None:
        """
        Record actual web searches consumed by a request.

        Args:
            actual_searches: Number of web searches from usage.server_tool_use.
        """
        async with self._lock:
            now = time.monotonic()
            self._requests.append((now, actual_searches))
            self._total_requests += 1
            self._total_searches += actual_searches

    def _cleanup_old_requests(self) -> None:
        """Remove requests older than the window."""
        cutoff = time.monotonic() - self.window_seconds
        while self._requests and self._requests[0][0] < cutoff:
            self._requests.popleft()

    async def get_stats(self) -> RateLimiterStats:
        """Get current rate limiter statistics."""
        async with self._lock:
            self._cleanup_old_requests()

            window_searches = sum(s for _, s in self._requests)

            return RateLimiterStats(
                total_requests=self._total_requests,
                total_searches=self._total_searches,
                window_requests=len(self._requests),
                current_rpm=window_searches,
                avg_searches_per_request=(
                    self._total_searches / self._total_requests
                    if self._total_requests > 0 else 0.0
                ),
            )

    async def get_current_usage(self) -> int:
        """Get current searches in the sliding window."""
        async with self._lock:
            self._cleanup_old_requests()
            return sum(s for _, s in self._requests)


class AdaptiveConcurrencyManager:
    """
    Dynamically adjusts concurrency based on actual search usage.

    Starts conservative and increases if companies use fewer searches.
    """

    def __init__(
        self,
        initial_concurrency: int = 3,
        max_concurrency: int = 10,
        target_searches_per_company: float = 7.0,
        max_rpm: int = 30,
    ):
        self.current_concurrency = initial_concurrency
        self.max_concurrency = max_concurrency
        self.target_searches = target_searches_per_company
        self.max_rpm = max_rpm
        self._lock = asyncio.Lock()
        self._recent_searches: Deque[int] = deque(maxlen=20)

    async def record_searches(self, searches: int) -> None:
        """Record searches used by a company."""
        async with self._lock:
            self._recent_searches.append(searches)
            self._adjust_concurrency()

    def _adjust_concurrency(self) -> None:
        """Adjust concurrency based on recent search patterns."""
        if len(self._recent_searches) < 5:
            return  # Not enough data

        avg_searches = sum(self._recent_searches) / len(self._recent_searches)

        # Calculate optimal concurrency
        # max_rpm / avg_searches = max parallel requests
        optimal = int(self.max_rpm / max(avg_searches, 1))
        optimal = min(optimal, self.max_concurrency)
        optimal = max(optimal, 1)

        # Gradual adjustment
        if optimal > self.current_concurrency:
            self.current_concurrency = min(
                self.current_concurrency + 1,
                optimal
            )
        elif optimal < self.current_concurrency - 1:
            self.current_concurrency = max(
                self.current_concurrency - 1,
                optimal
            )

    async def get_semaphore_value(self) -> int:
        """Get current concurrency value."""
        async with self._lock:
            return self.current_concurrency
