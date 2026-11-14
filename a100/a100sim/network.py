"""Keep implementation-only HTTP workers behind the public gRPC boundary."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit


def require_loopback(host: str) -> str:
    """Accept only literal loopback addresses, avoiding mutable DNS bindings."""
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ip_address(host)
    except ValueError as error:
        raise ValueError("Internal HTTP workers require a literal loopback address") from error
    if not address.is_loopback:
        raise ValueError("Internal HTTP workers must listen on loopback; expose the gRPC gateway")
    return str(address)


def require_worker_url(value: str) -> str:
    """Validate an internal worker origin without credentials, paths or query strings."""
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Internal workers require an HTTP loopback origin without credentials")
    host = require_loopback(parsed.hostname)
    if not parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Internal worker URLs require a port and no route, query or fragment")
    return f"http://{'[' + host + ']' if ':' in host else host}:{parsed.port}"
