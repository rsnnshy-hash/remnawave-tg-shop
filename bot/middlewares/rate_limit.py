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


# ============================================================================
# Aiogram Rate Limiting Middleware (for Telegram messages/callbacks)
# ============================================================================

from typing import Any, Awaitable, Callable, Set
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject


class TelegramRateLimitMiddleware(BaseMiddleware):
    """
    Middleware that implements rate limiting for Telegram messages/callbacks.
    Uses a sliding window algorithm to track request counts per user.
    """

    def __init__(
        self,
        messages_per_minute: int = 30,
        callbacks_per_minute: int = 60,
        admin_ids: Set[int] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.messages_limit = messages_per_minute
        self.callbacks_limit = callbacks_per_minute
        self.admin_ids = admin_ids or set()
        self._window_size = 60  # 1 minute

        # Store timestamps per user
        self._message_timestamps: Dict[int, list] = defaultdict(list)
        self._callback_timestamps: Dict[int, list] = defaultdict(list)
        self._cooldown_until: Dict[int, float] = {}

        logging.info(
            f"TelegramRateLimitMiddleware: messages={messages_per_minute}/min, "
            f"callbacks={callbacks_per_minute}/min, enabled={enabled}"
        )

    def _cleanup_timestamps(self, timestamps: list, current_time: float) -> list:
        cutoff = current_time - self._window_size
        return [ts for ts in timestamps if ts > cutoff]

    def _is_rate_limited(self, user_id: int, timestamps_dict: Dict, limit: int, now: float) -> bool:
        timestamps_dict[user_id] = self._cleanup_timestamps(timestamps_dict[user_id], now)
        if len(timestamps_dict[user_id]) >= limit:
            return True
        timestamps_dict[user_id].append(now)
        return False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not self.enabled:
            return await handler(event, data)

        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if not user_id or user_id in self.admin_ids:
            return await handler(event, data)

        now = time.time()

        # Check cooldown
        if now < self._cooldown_until.get(user_id, 0):
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("Подождите немного...", show_alert=False)
                except Exception:
                    pass
            return None

        # Check rate limit
        is_limited = False
        if isinstance(event, Message):
            is_limited = self._is_rate_limited(user_id, self._message_timestamps, self.messages_limit, now)
        elif isinstance(event, CallbackQuery):
            is_limited = self._is_rate_limited(user_id, self._callback_timestamps, self.callbacks_limit, now)

        if is_limited:
            logging.warning(f"Rate limit: user {user_id}")
            self._cooldown_until[user_id] = now + 30

            if isinstance(event, Message):
                try:
                    await event.answer("⚠️ Слишком много сообщений. Подождите 30 секунд.")
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("Подождите 30 секунд", show_alert=True)
                except Exception:
                    pass
            return None

        return await handler(event, data)
