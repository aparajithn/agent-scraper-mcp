"""Rate limiting middleware for free tier tracking."""
import time
from collections import defaultdict
from typing import Dict, Mapping, Tuple


def client_ip(headers: Mapping[str, str], fallback: str = "unknown") -> str:
    """Extract the client IP from proxy headers (case-insensitive)."""
    # Check X-Forwarded-For header (from proxies)
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    # Check X-Real-IP header
    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip

    # Fall back to direct connection
    return fallback


class RateLimiter:
    """Simple in-memory rate limiter with TTL."""

    def __init__(self, free_limit: int = 100, ttl_seconds: int = 86400):
        """
        Initialize rate limiter.

        Args:
            free_limit: Number of free requests per IP
            ttl_seconds: Time to live for rate limit counters (default 24h)
        """
        self.free_limit = free_limit
        self.ttl_seconds = ttl_seconds
        self.counters: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "reset_at": 0})

    def get_client_ip(self, request) -> str:
        """Extract client IP from an HTTP request."""
        return client_ip(request.headers, request.client.host if request.client else "unknown")

    def check_limit_ip(self, ip: str) -> Tuple[bool, int, int]:
        """
        Check (and increment) the free-tier counter for an IP.

        Returns:
            (allowed, remaining, reset_at)
        """
        now = time.time()

        # Get or initialize counter
        counter = self.counters[ip]

        # Reset if TTL expired
        if counter["reset_at"] < now:
            counter["count"] = 0
            counter["reset_at"] = now + self.ttl_seconds

        # Check limit
        if counter["count"] >= self.free_limit:
            return False, 0, int(counter["reset_at"])

        # Increment counter
        counter["count"] += 1
        remaining = self.free_limit - counter["count"]

        return True, remaining, int(counter["reset_at"])

    def check_limit(self, request) -> Tuple[bool, int, int]:
        """Check if request is within the free tier limit."""
        return self.check_limit_ip(self.get_client_ip(request))

    def cleanup_expired(self):
        """Remove expired counters (should be called periodically)."""
        now = time.time()
        expired = [ip for ip, counter in self.counters.items() if counter["reset_at"] < now]
        for ip in expired:
            del self.counters[ip]