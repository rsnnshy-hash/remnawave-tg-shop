"""Sentry error monitoring integration."""
import logging
from typing import Optional

try:
    import sentry_sdk
    from sentry_sdk.integrations.aiohttp import AioHttpIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
    sentry_sdk = None


def init_sentry(dsn: Optional[str], environment: str = "production") -> bool:
    """
    Initialize Sentry error monitoring.

    Args:
        dsn: Sentry DSN (Data Source Name). If None or empty, Sentry is disabled.
        environment: Environment name (e.g., 'production', 'staging')

    Returns:
        True if Sentry was initialized, False otherwise
    """
    if not SENTRY_AVAILABLE:
        logging.warning("Sentry SDK not installed. Error monitoring disabled.")
        return False

    if not dsn:
        logging.info("Sentry DSN not configured. Error monitoring disabled.")
        return False

    try:
        sentry_logging = LoggingIntegration(
            level=logging.INFO,        # Capture info and above as breadcrumbs
            event_level=logging.ERROR  # Send errors as events
        )

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            integrations=[
                sentry_logging,
                AioHttpIntegration(),
                SqlalchemyIntegration(),
            ],
            # Set traces_sample_rate to capture performance data
            traces_sample_rate=0.1,
            # Set profiles_sample_rate for profiling (optional)
            profiles_sample_rate=0.1,
            # Send user context
            send_default_pii=False,
            # Ignore certain exceptions
            ignore_errors=[
                KeyboardInterrupt,
                SystemExit,
            ],
        )

        logging.info(f"Sentry initialized for environment: {environment}")
        return True

    except Exception as e:
        logging.error(f"Failed to initialize Sentry: {e}")
        return False


def capture_exception(error: Exception, **kwargs) -> Optional[str]:
    """
    Capture and send an exception to Sentry.

    Args:
        error: The exception to capture
        **kwargs: Additional context to attach

    Returns:
        Event ID if sent, None otherwise
    """
    if not SENTRY_AVAILABLE or not sentry_sdk:
        return None

    try:
        with sentry_sdk.push_scope() as scope:
            for key, value in kwargs.items():
                scope.set_extra(key, value)
            return sentry_sdk.capture_exception(error)
    except Exception as e:
        logging.warning(f"Failed to capture exception in Sentry: {e}")
        return None


def capture_message(message: str, level: str = "info", **kwargs) -> Optional[str]:
    """
    Send a message to Sentry.

    Args:
        message: The message to send
        level: Log level ('debug', 'info', 'warning', 'error', 'fatal')
        **kwargs: Additional context to attach

    Returns:
        Event ID if sent, None otherwise
    """
    if not SENTRY_AVAILABLE or not sentry_sdk:
        return None

    try:
        with sentry_sdk.push_scope() as scope:
            for key, value in kwargs.items():
                scope.set_extra(key, value)
            return sentry_sdk.capture_message(message, level=level)
    except Exception as e:
        logging.warning(f"Failed to capture message in Sentry: {e}")
        return None


def set_user_context(user_id: int, username: Optional[str] = None):
    """
    Set user context for Sentry events.

    Args:
        user_id: Telegram user ID
        username: Telegram username (optional)
    """
    if not SENTRY_AVAILABLE or not sentry_sdk:
        return

    try:
        sentry_sdk.set_user({
            "id": str(user_id),
            "username": username,
        })
    except Exception:
        pass


def add_breadcrumb(message: str, category: str = "info", data: Optional[dict] = None):
    """
    Add a breadcrumb for debugging context.

    Args:
        message: Breadcrumb message
        category: Category (e.g., 'payment', 'user', 'admin')
        data: Additional data to attach
    """
    if not SENTRY_AVAILABLE or not sentry_sdk:
        return

    try:
        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            data=data or {},
        )
    except Exception:
        pass
