"""
Rate limiting middleware for webhook endpoints.
Protects against flood attacks and abuse.
"""
import time
import logging
from collections import defaultdict
from typing import Dict, Tuple
from aiohttp import web

# Rate limit configuration
RATE_LIMIT_REQUESTS = 100  # Max requests per window
RATE_LIMIT_WINDOW = 60  # Window in seconds (1 minute)

# In-memory storage for rate limiting
# Key: IP address, Value: (request_count, window_start_time)
_rate_limit_storage: Dict[str, Tuple[int, float]] = defaultdict(lambda: (0, 0.0))


def _get_client_ip(request: web.Request) -> str:
    """Extract client IP from request."""
    # Check X-Forwarded-For first
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    # Check X-Real-IP
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()

    # Fallback to direct connection
    if request.transport:
        peername = request.transport.get_extra_info("peername")
        if peername:
            return peername[0]

    return "unknown"


def check_rate_limit(ip: str) -> Tuple[bool, int]:
    """
    Check if IP is within rate limit.

    Returns:
        Tuple of (is_allowed, remaining_requests)
    """
    current_time = time.time()
    count, window_start = _rate_limit_storage[ip]

    # Reset window if expired
    if current_time - window_start > RATE_LIMIT_WINDOW:
        _rate_limit_storage[ip] = (1, current_time)
        return True, RATE_LIMIT_REQUESTS - 1

    # Check if limit exceeded
    if count >= RATE_LIMIT_REQUESTS:
        return False, 0

    # Increment counter
    _rate_limit_storage[ip] = (count + 1, window_start)
    return True, RATE_LIMIT_REQUESTS - count - 1


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    """
    Middleware to apply rate limiting on webhook endpoints.
    """
    # Only apply to webhook endpoints
    path = request.path
    if not path.startswith("/webhook"):
        return await handler(request)

    client_ip = _get_client_ip(request)
    is_allowed, remaining = check_rate_limit(client_ip)

    if not is_allowed:
        logging.warning(f"Rate limit exceeded for IP: {client_ip} on path: {path}")
        return web.Response(
            status=429,
            text="Too Many Requests",
            headers={
                "Retry-After": str(RATE_LIMIT_WINDOW),
                "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                "X-RateLimit-Remaining": "0",
            }
        )

    response = await handler(request)

    # Add rate limit headers to response
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    return response


def cleanup_rate_limit_storage():
    """
    Cleanup expired entries from rate limit storage.
    Call periodically to prevent memory leaks.
    """
    current_time = time.time()
    expired_keys = [
        ip for ip, (_, window_start) in _rate_limit_storage.items()
        if current_time - window_start > RATE_LIMIT_WINDOW * 2
    ]
    for ip in expired_keys:
        del _rate_limit_storage[ip]

    if expired_keys:
        logging.debug(f"Rate limit cleanup: removed {len(expired_keys)} expired entries")
