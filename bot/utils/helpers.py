"""Common helper utilities for the bot."""
import logging
from typing import Optional, Callable, Any
from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.middlewares.i18n import JsonI18n


def get_i18n_func(i18n: Optional[JsonI18n], lang: str) -> Callable[..., str]:
    """
    Create a translation function for the given language.

    Usage:
        _ = get_i18n_func(i18n, current_lang)
        text = _("some_key", param=value)
    """
    if i18n:
        return lambda key, **kwargs: i18n.gettext(lang, key, **kwargs)
    return lambda key, **kwargs: key


async def safe_callback_answer(
    callback: types.CallbackQuery,
    text: Optional[str] = None,
    show_alert: bool = False,
    context: str = ""
) -> bool:
    """
    Safely answer a callback query, catching and logging any errors.

    Args:
        callback: The callback query to answer
        text: Optional text to show in the answer
        show_alert: Whether to show as alert popup
        context: Context string for logging (e.g. function name)

    Returns:
        True if successful, False otherwise
    """
    try:
        await callback.answer(text=text, show_alert=show_alert)
        return True
    except TelegramBadRequest as e:
        # Query is too old or already answered - common and not critical
        logging.debug(f"Callback answer failed ({context}): {e}")
        return False
    except Exception as e:
        logging.warning(f"Unexpected error answering callback ({context}): {e}")
        return False


async def safe_edit_message(
    message: types.Message,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = None,
    disable_web_page_preview: bool = False,
    context: str = ""
) -> bool:
    """
    Safely edit a message, catching and logging any errors.

    Returns:
        True if successful, False otherwise
    """
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview
        )
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logging.debug(f"Message not modified ({context})")
            return True  # Not really an error
        logging.warning(f"Failed to edit message ({context}): {e}")
        return False
    except Exception as e:
        logging.warning(f"Unexpected error editing message ({context}): {e}")
        return False


async def safe_send_message(
    target: types.Message,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = None,
    disable_web_page_preview: bool = False,
    context: str = ""
) -> Optional[types.Message]:
    """
    Safely send a message, catching and logging any errors.

    Returns:
        The sent message if successful, None otherwise
    """
    try:
        return await target.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview
        )
    except TelegramForbiddenError:
        logging.debug(f"User blocked the bot ({context})")
        return None
    except Exception as e:
        logging.warning(f"Failed to send message ({context}): {e}")
        return None


def parse_callback_int(data: str, index: int, default: int = 0) -> int:
    """
    Safely parse an integer from callback data at given index.

    Args:
        data: The callback data string (e.g. "action:123:456")
        index: The index after splitting by ":"
        default: Default value if parsing fails

    Returns:
        The parsed integer or default value
    """
    try:
        parts = data.split(":")
        return int(parts[index])
    except (ValueError, IndexError) as e:
        logging.debug(f"Failed to parse int from callback data at index {index}: {e}")
        return default


def sanitize_error_for_user(error: Exception) -> str:
    """
    Sanitize an error message for user display.
    Never expose internal details, stack traces, or sensitive info.

    Returns:
        A safe, generic error message
    """
    # Never expose the actual exception to users
    # Log it instead and return generic message
    logging.error(f"Error sanitized for user: {error}", exc_info=True)
    return "An error occurred. Please try again later."
