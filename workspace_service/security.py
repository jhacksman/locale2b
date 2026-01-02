"""
Security middleware and utilities for the Firecracker Workspace Service.

This module provides:
- API key authentication
- Rate limiting
- Path traversal protection
- Request size limits
"""

import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """Security configuration loaded from environment variables."""

    # API Key authentication
    api_key_enabled: bool
    api_keys: set[str]  # Set of valid API key hashes (SHA256)
    api_key_header: str

    # Rate limiting
    rate_limit_enabled: bool
    rate_limit_requests: int  # Max requests per window
    rate_limit_window_seconds: int  # Time window in seconds

    # Request size limits
    max_request_size_bytes: int

    # Path security
    allowed_path_prefixes: list[str]  # Allowed path prefixes for file operations

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        """Load security configuration from environment variables."""
        # Parse API keys (comma-separated, stored as SHA256 hashes)
        raw_keys = os.environ.get("API_KEYS", "")
        api_keys = set()
        if raw_keys:
            for key in raw_keys.split(","):
                key = key.strip()
                if key:
                    # Store hash of key for secure comparison
                    api_keys.add(hashlib.sha256(key.encode()).hexdigest())

        return cls(
            # API Key authentication
            api_key_enabled=os.environ.get("API_KEY_ENABLED", "false").lower()
            == "true",
            api_keys=api_keys,
            api_key_header=os.environ.get("API_KEY_HEADER", "X-API-Key"),
            # Rate limiting
            rate_limit_enabled=os.environ.get("RATE_LIMIT_ENABLED", "true").lower()
            == "true",
            rate_limit_requests=int(os.environ.get("RATE_LIMIT_REQUESTS", "100")),
            rate_limit_window_seconds=int(
                os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
            # Request size limits (default 10MB)
            max_request_size_bytes=int(
                os.environ.get("MAX_REQUEST_SIZE_BYTES", str(10 * 1024 * 1024))
            ),
            # Path security - default allows /workspace and /tmp
            allowed_path_prefixes=os.environ.get(
                "ALLOWED_PATH_PREFIXES", "/workspace,/tmp"
            ).split(","),
        )


# Global security config
_security_config: Optional[SecurityConfig] = None


def get_security_config() -> SecurityConfig:
    """Get the global security configuration."""
    global _security_config
    if _security_config is None:
        _security_config = SecurityConfig.from_env()
    return _security_config


def reset_security_config() -> None:
    """Reset the security configuration (useful for testing)."""
    global _security_config
    _security_config = None


class RateLimiter:
    """Simple in-memory rate limiter using sliding window."""

    def __init__(self):
        # Dict of client_id -> list of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(
        self, client_id: str, max_requests: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Check if a request is allowed for the given client.

        Returns:
            Tuple of (is_allowed, requests_remaining)
        """
        now = time.time()
        window_start = now - window_seconds

        # Clean old requests outside the window
        self._requests[client_id] = [
            ts for ts in self._requests[client_id] if ts > window_start
        ]

        current_count = len(self._requests[client_id])

        if current_count >= max_requests:
            return False, 0

        # Record this request
        self._requests[client_id].append(now)
        return True, max_requests - current_count - 1

    def get_retry_after(self, client_id: str, window_seconds: int) -> int:
        """Get seconds until the client can make another request."""
        if not self._requests[client_id]:
            return 0

        oldest_request = min(self._requests[client_id])
        retry_after = int(oldest_request + window_seconds - time.time())
        return max(0, retry_after)


# Global rate limiter instance
rate_limiter = RateLimiter()


def get_client_id(request: Request) -> str:
    """Extract client identifier from request for rate limiting."""
    # Use API key if present, otherwise use IP
    config = get_security_config()
    api_key = request.headers.get(config.api_key_header)
    if api_key:
        return f"key:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"

    # Fall back to IP address
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"

    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


def verify_api_key(request: Request) -> bool:
    """Verify the API key in the request headers.

    Returns True if authentication is disabled or key is valid.
    """
    config = get_security_config()

    if not config.api_key_enabled:
        return True

    api_key = request.headers.get(config.api_key_header)
    if not api_key:
        return False

    # Compare hash of provided key with stored hashes
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    return key_hash in config.api_keys


def validate_path(path: str) -> tuple[bool, str]:
    """Validate a file path for security issues.

    Checks for:
    - Path traversal attempts (../)
    - Absolute paths outside allowed prefixes
    - Null bytes
    - Other suspicious patterns

    Returns:
        Tuple of (is_valid, error_message)
    """
    config = get_security_config()

    # Check for null bytes
    if "\x00" in path:
        return False, "Path contains null bytes"

    # Normalize the path to resolve any .. or . components
    # This helps detect path traversal attempts
    normalized = os.path.normpath(path)

    # Check for path traversal attempts
    if ".." in path or ".." in normalized:
        # Even after normalization, reject if original had ..
        if ".." in path:
            return False, "Path traversal attempt detected"

    # If path is absolute, check it's within allowed prefixes
    if os.path.isabs(normalized):
        allowed = False
        for prefix in config.allowed_path_prefixes:
            prefix = prefix.strip()
            if normalized.startswith(prefix):
                allowed = True
                break

        if not allowed:
            return False, f"Path outside allowed directories: {normalized}"

    # Check for suspicious patterns
    suspicious_patterns = [
        r"/etc/",
        r"/proc/",
        r"/sys/",
        r"/dev/",
        r"/root/",
        r"/home/(?!workspace)",  # Allow /home/workspace but not other home dirs
        r"\.\.+",  # Multiple dots
    ]

    for pattern in suspicious_patterns:
        if re.search(pattern, normalized):
            return False, f"Suspicious path pattern detected: {normalized}"

    return True, ""


class SecurityMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces security policies."""

    # Paths that don't require authentication
    PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next: Callable):
        config = get_security_config()

        # Check request size
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > config.max_request_size_bytes:
                    logger.warning(
                        f"Request too large: {size} bytes from {get_client_id(request)}"
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": f"Request too large. Maximum size is "
                            f"{config.max_request_size_bytes} bytes"
                        },
                    )
            except ValueError:
                pass

        # Check API key authentication (skip for public paths)
        if request.url.path not in self.PUBLIC_PATHS:
            if not verify_api_key(request):
                logger.warning(
                    f"Unauthorized request to {request.url.path} "
                    f"from {get_client_id(request)}"
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                    headers={"WWW-Authenticate": f'ApiKey header="{config.api_key_header}"'},
                )

        # Check rate limit
        if config.rate_limit_enabled:
            client_id = get_client_id(request)
            allowed, remaining = rate_limiter.is_allowed(
                client_id, config.rate_limit_requests, config.rate_limit_window_seconds
            )

            if not allowed:
                retry_after = rate_limiter.get_retry_after(
                    client_id, config.rate_limit_window_seconds
                )
                logger.warning(f"Rate limit exceeded for {client_id}")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(config.rate_limit_requests),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                    },
                )

        # Process the request
        response = await call_next(request)

        # Add rate limit headers to response
        if config.rate_limit_enabled:
            client_id = get_client_id(request)
            _, remaining = rate_limiter.is_allowed(
                client_id, config.rate_limit_requests, config.rate_limit_window_seconds
            )
            response.headers["X-RateLimit-Limit"] = str(config.rate_limit_requests)
            response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))

        return response


def require_valid_path(path: str) -> str:
    """Validate a path and raise HTTPException if invalid.

    Use this in endpoint handlers to validate file paths.
    """
    is_valid, error = validate_path(path)
    if not is_valid:
        logger.warning(f"Invalid path rejected: {path} - {error}")
        raise HTTPException(status_code=400, detail=error)
    return path
