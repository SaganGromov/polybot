"""
AI Rate Limiter Service

Provides rate limiting and request queueing for AI API calls to prevent
overwhelming the Gemini API when watching many wallets.

Features:
- Token bucket rate limiting with configurable RPS
- Semaphore-based concurrency control
- Async context manager for easy integration
- Logging for monitoring queue depth
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AIRateLimiter:
    """
    Token bucket rate limiter with request queue for AI API calls.
    
    Uses a combination of:
    1. Token bucket for rate limiting (refills over time)
    2. Semaphore for limiting concurrent requests
    3. Timeout to prevent unbounded waiting
    """
    
    def __init__(
        self,
        requests_per_second: float = 5.0,
        max_concurrent: int = 10,
        queue_timeout: float = 120.0,
        burst_capacity: Optional[int] = None
    ):
        """
        Initialize the rate limiter.
        
        Args:
            requests_per_second: Max sustained request rate (default: 5 RPS = 300/min)
            max_concurrent: Max simultaneous in-flight requests (default: 10)
            queue_timeout: Seconds to wait for a slot before giving up (default: 120)
            burst_capacity: Max tokens for burst handling (default: 2x RPS)
        """
        self.rps = requests_per_second
        self.max_concurrent = max_concurrent
        self.queue_timeout = queue_timeout
        self.burst_capacity = burst_capacity or max(int(requests_per_second * 2), 5)
        
        # Token bucket state
        self._tokens = float(self.burst_capacity)
        self._last_refill = time.monotonic()
        self._token_lock = asyncio.Lock()
        
        # Concurrency control
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # Metrics
        self._queue_depth = 0
        self._total_acquired = 0
        self._total_timeouts = 0
        
        logger.info(
            f"🚦 AIRateLimiter initialized: {requests_per_second} RPS, "
            f"{max_concurrent} concurrent, {queue_timeout}s timeout, "
            f"burst capacity: {self.burst_capacity}"
        )
    
    async def _refill_tokens(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self.rps
        self._tokens = min(self._tokens + new_tokens, self.burst_capacity)
        self._last_refill = now
    
    async def _acquire_token(self) -> bool:
        """
        Attempt to acquire a rate limit token.
        
        Returns True if acquired, False if would exceed rate limit.
        """
        async with self._token_lock:
            await self._refill_tokens()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False
    
    async def _wait_for_token(self, timeout: float) -> bool:
        """
        Wait for a token to become available.
        
        Args:
            timeout: Max seconds to wait
            
        Returns:
            True if token acquired, False if timeout
        """
        deadline = time.monotonic() + timeout
        
        while True:
            if await self._acquire_token():
                return True
            
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            
            # Calculate sleep time until next token
            async with self._token_lock:
                tokens_needed = 1.0 - self._tokens
                wait_time = tokens_needed / self.rps
            
            # Sleep for the minimum of wait_time or remaining timeout
            sleep_time = min(wait_time, remaining, 0.1)  # Cap at 100ms for responsiveness
            await asyncio.sleep(sleep_time)
    
    async def acquire(self):
        """
        Acquire a slot for making an AI request.
        
        This is an async context manager that handles both rate limiting
        and concurrency control.
        
        Usage:
            async with rate_limiter.acquire():
                result = await make_api_call()
        
        Raises:
            asyncio.TimeoutError if queue timeout exceeded
        """
        return _RateLimitContext(self)
    
    def update_config(
        self,
        requests_per_second: Optional[float] = None,
        max_concurrent: Optional[int] = None,
        queue_timeout: Optional[float] = None
    ):
        """Update rate limiter configuration dynamically."""
        if requests_per_second is not None:
            self.rps = requests_per_second
            self.burst_capacity = max(int(requests_per_second * 2), 5)
            logger.info(f"🚦 Rate limit updated: {requests_per_second} RPS")
        
        if max_concurrent is not None:
            # Recreate semaphore with new limit
            self._semaphore = asyncio.Semaphore(max_concurrent)
            self.max_concurrent = max_concurrent
            logger.info(f"🚦 Max concurrent updated: {max_concurrent}")
        
        if queue_timeout is not None:
            self.queue_timeout = queue_timeout
            logger.info(f"🚦 Queue timeout updated: {queue_timeout}s")
    
    @property
    def stats(self) -> dict:
        """Get current rate limiter statistics."""
        return {
            "queue_depth": self._queue_depth,
            "total_acquired": self._total_acquired,
            "total_timeouts": self._total_timeouts,
            "available_tokens": self._tokens,
            "rps": self.rps,
            "max_concurrent": self.max_concurrent
        }


class _RateLimitContext:
    """Async context manager for rate-limited requests."""
    
    def __init__(self, limiter: AIRateLimiter):
        self.limiter = limiter
    
    async def __aenter__(self):
        self.limiter._queue_depth += 1
        
        if self.limiter._queue_depth > 5:
            logger.info(f"⏳ AI queue depth: {self.limiter._queue_depth}")
        
        try:
            # First, wait for rate limit token
            got_token = await self.limiter._wait_for_token(self.limiter.queue_timeout)
            if not got_token:
                self.limiter._total_timeouts += 1
                self.limiter._queue_depth -= 1
                raise asyncio.TimeoutError(
                    f"Rate limit queue timeout after {self.limiter.queue_timeout}s"
                )
            
            # Then, acquire semaphore for concurrency control
            await asyncio.wait_for(
                self.limiter._semaphore.acquire(),
                timeout=self.limiter.queue_timeout
            )
            
            self.limiter._total_acquired += 1
            return self
            
        except asyncio.TimeoutError:
            self.limiter._total_timeouts += 1
            self.limiter._queue_depth -= 1
            raise
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.limiter._semaphore.release()
        self.limiter._queue_depth -= 1
        return False
