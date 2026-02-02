"""Input validation utilities for the bot."""
import re
import logging
from typing import Optional, Tuple
from decimal import Decimal, InvalidOperation


# Regex patterns for validation
ALPHANUMERIC_PATTERN = re.compile(r'^[A-Za-z0-9_\-]+$')
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_]{3,32}$')
START_PARAM_PATTERN = re.compile(r'^[A-Za-z0-9_\-]{2,64}$')


def validate_ad_source(source: str) -> Tuple[bool, str]:
    """
    Validate an ad campaign source string.

    Args:
        source: The source string to validate

    Returns:
        Tuple of (is_valid, error_message or cleaned_source)
    """
    if not source:
        return False, "Source cannot be empty"

    source = source.strip()

    if len(source) > 64:
        return False, "Source must be 64 characters or less"

    if len(source) < 1:
        return False, "Source cannot be empty"

    # Allow alphanumeric, spaces, underscores, hyphens
    if not re.match(r'^[\w\s\-]+$', source, re.UNICODE):
        return False, "Source contains invalid characters"

    return True, source


def validate_start_param(start_param: str) -> Tuple[bool, str]:
    """
    Validate a start parameter for deep links.

    Args:
        start_param: The start parameter to validate

    Returns:
        Tuple of (is_valid, error_message or cleaned_param)
    """
    if not start_param:
        return False, "Start parameter cannot be empty"

    start_param = start_param.strip()

    if not START_PARAM_PATTERN.match(start_param):
        return False, "Start parameter must be 2-64 alphanumeric characters (with _ or -)"

    return True, start_param


def parse_float_safe(value: str, min_val: float = 0, max_val: float = 1e8) -> Tuple[bool, float, str]:
    """
    Safely parse a float value from user input.

    Handles:
    - Comma as decimal separator
    - Whitespace
    - Range validation

    Args:
        value: The string value to parse
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        Tuple of (is_valid, parsed_value, error_message)
    """
    if not value:
        return False, 0.0, "Value cannot be empty"

    # Clean up the value
    cleaned = value.strip().replace(",", ".").replace(" ", "")

    # Remove currency symbols and common prefixes
    cleaned = cleaned.lstrip("$€₽")

    try:
        # Use Decimal for precise parsing
        decimal_val = Decimal(cleaned)
        float_val = float(decimal_val)

        if float_val < min_val:
            return False, 0.0, f"Value must be at least {min_val}"

        if float_val > max_val:
            return False, 0.0, f"Value must be at most {max_val}"

        return True, float_val, ""

    except (InvalidOperation, ValueError) as e:
        logging.debug(f"Failed to parse float from '{value}': {e}")
        return False, 0.0, "Invalid number format"


def parse_int_safe(value: str, min_val: int = 0, max_val: int = 2**31) -> Tuple[bool, int, str]:
    """
    Safely parse an integer value from user input.

    Args:
        value: The string value to parse
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        Tuple of (is_valid, parsed_value, error_message)
    """
    if not value:
        return False, 0, "Value cannot be empty"

    cleaned = value.strip()

    try:
        int_val = int(cleaned)

        if int_val < min_val:
            return False, 0, f"Value must be at least {min_val}"

        if int_val > max_val:
            return False, 0, f"Value must be at most {max_val}"

        return True, int_val, ""

    except ValueError as e:
        logging.debug(f"Failed to parse int from '{value}': {e}")
        return False, 0, "Invalid integer format"


def sanitize_html(text: str) -> str:
    """
    Sanitize text for safe use in HTML messages.

    Escapes HTML special characters to prevent XSS.
    """
    if not text:
        return ""

    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def validate_callback_data(
    data: str,
    expected_prefix: str,
    min_parts: int = 1
) -> Tuple[bool, list, str]:
    """
    Validate callback data format.

    Args:
        data: The callback data string
        expected_prefix: Expected prefix (e.g., "admin_ads")
        min_parts: Minimum number of parts after splitting by ":"

    Returns:
        Tuple of (is_valid, parts_list, error_message)
    """
    if not data:
        return False, [], "Empty callback data"

    parts = data.split(":")

    if len(parts) < min_parts:
        return False, [], f"Expected at least {min_parts} parts in callback data"

    if expected_prefix and not data.startswith(expected_prefix):
        return False, [], f"Callback data must start with {expected_prefix}"

    return True, parts, ""
