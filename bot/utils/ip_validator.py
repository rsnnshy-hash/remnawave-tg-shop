"""
IP validation utilities for webhook security.
"""
import ipaddress
import logging
from typing import Optional, Set
from aiohttp import web


# YooKassa official IP ranges
# https://yookassa.ru/developers/using-api/webhooks
YOOKASSA_IP_RANGES = [
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.154.128/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
]

# Pre-computed networks for faster lookup
_YOOKASSA_NETWORKS = [ipaddress.ip_network(r) for r in YOOKASSA_IP_RANGES]


def get_client_ip(request: web.Request) -> Optional[str]:
    """
    Extract real client IP from request.
    Checks X-Forwarded-For, X-Real-IP headers first (for reverse proxy setups),
    then falls back to direct connection IP.
    """
    # Check X-Forwarded-For (may contain multiple IPs)
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # Take the first IP (original client)
        ip = x_forwarded_for.split(",")[0].strip()
        if ip:
            return ip

    # Check X-Real-IP
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()

    # Fallback to direct connection
    if request.transport:
        peername = request.transport.get_extra_info("peername")
        if peername:
            return peername[0]

    return None


def is_yookassa_ip(ip_str: Optional[str]) -> bool:
    """
    Check if the given IP address belongs to YooKassa.

    Args:
        ip_str: IP address string to check

    Returns:
        True if IP is from YooKassa, False otherwise
    """
    if not ip_str:
        return False

    try:
        ip = ipaddress.ip_address(ip_str)
        for network in _YOOKASSA_NETWORKS:
            if ip in network:
                return True
        return False
    except ValueError as e:
        logging.warning(f"Invalid IP address format: {ip_str} - {e}")
        return False


def validate_yookassa_request(request: web.Request) -> tuple[bool, str]:
    """
    Validate that request comes from YooKassa servers.

    Args:
        request: aiohttp request object

    Returns:
        Tuple of (is_valid, client_ip)
    """
    client_ip = get_client_ip(request)

    if not client_ip:
        logging.warning("YooKassa webhook: Could not determine client IP")
        return False, "unknown"

    is_valid = is_yookassa_ip(client_ip)

    if not is_valid:
        logging.warning(f"YooKassa webhook: Rejected request from non-YooKassa IP: {client_ip}")

    return is_valid, client_ip
