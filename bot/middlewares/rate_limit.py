"""
Rate limiting middleware for webhook endpoints and Telegram messages.
Protects against flood attacks and abuse.
"""
import time
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, Optional, Set, Tuple, Union

from aiohttp import web
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

# Rate limit configuration
RATE_LIMIT_REQUESTS = 100  # Max requests per window
RATE_LIMIT_WINDOW = 60  # Window in seconds (1 minute)

# Maximum entries before forced cleanup (prevents memory leak)
_MAX_STORAGE_SIZE = 10_000

# In-memory storage for rate limiting
# Key: IP address, Value: (request_count, window_start_time)
_rate_limit_storage: Dict[str, Tuple[int, float]] = defaultdict(lambda: (0, 0.0))

# Known trusted proxy IPs (localhost, Docker bridge networks)
_TRUSTED_PROXIES = frozenset({
    "127.0.0.1", "::1",
    "172.17.0.1", "172.18.0.1", "172.19.0.1", "172.20.0.1",
})


def _get_client_ip(request: web.Request) -> str:
    """Extract client IP from request.

    Only trusts proxy headers (X-Forwarded-For, X-Real-IP) if the direct
    connection comes from a known trusted proxy (localhost / Docker bridge).
    This prevents attackers from spoofing X-Forwarded-For to bypass rate limits.
    """
    # Determine direct connection IP first
    direct_ip = "unknown"
    if request.transport:
        peername = request.transport.get_extra_info("peername")
        if peername:
            direct_ip = peername[0]

    # Only trust proxy headers from known trusted sources
    if direct_ip in _TRUSTED_PROXIES:
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()

        x_real_ip = request.headers.get("X-Real-IP")
        if x_real_ip:
            return x_real_ip.strip()

    return direct_ip


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

    # Auto-cleanup when storage grows too large (prevents memory leak)
    if len(_rate_limit_storage) > _MAX_STORAGE_SIZE:
        cleanup_rate_limit_storage()

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
    Called automatically when storage exceeds _MAX_STORAGE_SIZE.
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


# ---------------------------------------------------------------------------
# Telegram per-user rate limiter (aiogram BaseMiddleware)
# ---------------------------------------------------------------------------

# Maximum tracked users before forced cleanup
_TG_MAX_STORAGE_SIZE = 50_000


class TelegramRateLimitMiddleware(BaseMiddleware):
    """
    Aiogram middleware that rate-limits per Telegram user.

    Tracks message and callback_query counts per user within a rolling
    60-second window.  Admin users are always allowed through.
    """

    def __init__(
        self,
        messages_per_minute: int = 30,
        callbacks_per_minute: int = 60,
        admin_ids: Optional[Set[int]] = None,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self.messages_per_minute = messages_per_minute
        self.callbacks_per_minute = callbacks_per_minute
        self.admin_ids: Set[int] = admin_ids or set()
        self.enabled = enabled
        # Key: (user_id, event_type), Value: list of timestamps
        self._buckets: Dict[tuple, list] = defaultdict(list)

    # ---- internal helpers --------------------------------------------------

    def _cleanup_bucket(self, key: tuple, now: float) -> list:
        """Remove timestamps older than 60 seconds and return the bucket."""
        bucket = self._buckets[key]
        cutoff = now - 60.0
        # Fast: timestamps are appended in order, so we can bisect
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        return bucket

    def _check(self, user_id: int, event_type: str, limit: int) -> bool:
        """
        Return True if the user is within the rate limit for *event_type*.
        If allowed, the current timestamp is recorded.
        """
        now = time.monotonic()
        key = (user_id, event_type)
        bucket = self._cleanup_bucket(key, now)

        if len(bucket) >= limit:
            return False  # rate-limited

        bucket.append(now)
        return True

    def _maybe_gc(self) -> None:
        """Evict stale entries when storage grows too large."""
        if len(self._buckets) <= _TG_MAX_STORAGE_SIZE:
            return
        now = time.monotonic()
        cutoff = now - 120.0
        stale = [k for k, v in self._buckets.items() if not v or v[-1] < cutoff]
        for k in stale:
            del self._buckets[k]
        if stale:
            logging.debug("TG rate limit GC: removed %d stale buckets", len(stale))

    # ---- aiogram middleware entry point ------------------------------------

    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any],
    ) -> Any:
        if not self.enabled:
            return await handler(event, data)

        user = event.from_user
        if user is None:
            return await handler(event, data)

        # Admins are never rate-limited
        if user.id in self.admin_ids:
            return await handler(event, data)

        # Periodic garbage collection
        self._maybe_gc()

        # Determine event type and its limit
        if isinstance(event, Message):
            event_type = "message"
            limit = self.messages_per_minute
        elif isinstance(event, CallbackQuery):
            event_type = "callback"
            limit = self.callbacks_per_minute
        else:
            return await handler(event, data)

        if not self._check(user.id, event_type, limit):
            logging.warning(
                "Telegram rate limit exceeded: user=%d type=%s limit=%d/min",
                user.id, event_type, limit,
            )
            # Silently drop the update (don't spam the user)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Too many requests. Please wait a moment.", show_alert=False)
                except Exception:
                    pass
            return  # drop

        return await handler(event, data)
