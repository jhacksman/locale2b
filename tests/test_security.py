"""
Unit tests for the security module.

These tests can run without KVM hardware.
"""

import hashlib
import os
import time

# Set test environment variables before importing
os.environ["API_KEY_ENABLED"] = "true"
os.environ["API_KEYS"] = "test-key-1,test-key-2"
os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["RATE_LIMIT_REQUESTS"] = "5"
os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "10"
os.environ["MAX_REQUEST_SIZE_BYTES"] = "1048576"  # 1MB
os.environ["ALLOWED_PATH_PREFIXES"] = "/workspace,/tmp"

from workspace_service.security import (
    RateLimiter,
    SecurityConfig,
    get_security_config,
    reset_security_config,
    validate_path,
)


class TestSecurityConfig:
    """Tests for SecurityConfig class."""

    def setup_method(self):
        """Reset config before each test."""
        reset_security_config()

    def test_from_env_loads_api_keys(self):
        """Test that API keys are loaded and hashed."""
        os.environ["API_KEY_ENABLED"] = "true"
        os.environ["API_KEYS"] = "secret1,secret2"

        reset_security_config()
        config = SecurityConfig.from_env()

        assert config.api_key_enabled is True
        assert len(config.api_keys) == 2

        # Keys should be stored as SHA256 hashes
        expected_hash1 = hashlib.sha256(b"secret1").hexdigest()
        expected_hash2 = hashlib.sha256(b"secret2").hexdigest()
        assert expected_hash1 in config.api_keys
        assert expected_hash2 in config.api_keys

    def test_from_env_disabled_auth(self):
        """Test that auth can be disabled."""
        os.environ["API_KEY_ENABLED"] = "false"

        reset_security_config()
        config = SecurityConfig.from_env()

        assert config.api_key_enabled is False

    def test_from_env_rate_limit_settings(self):
        """Test rate limit configuration."""
        os.environ["RATE_LIMIT_ENABLED"] = "true"
        os.environ["RATE_LIMIT_REQUESTS"] = "100"
        os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"

        reset_security_config()
        config = SecurityConfig.from_env()

        assert config.rate_limit_enabled is True
        assert config.rate_limit_requests == 100
        assert config.rate_limit_window_seconds == 60

    def test_from_env_request_size_limit(self):
        """Test request size limit configuration."""
        os.environ["MAX_REQUEST_SIZE_BYTES"] = "5242880"  # 5MB

        reset_security_config()
        config = SecurityConfig.from_env()

        assert config.max_request_size_bytes == 5242880

    def test_get_security_config_singleton(self):
        """Test that get_security_config returns the same instance."""
        reset_security_config()
        config1 = get_security_config()
        config2 = get_security_config()

        assert config1 is config2


class TestPathValidation:
    """Tests for path validation."""

    def setup_method(self):
        """Reset config before each test."""
        os.environ["ALLOWED_PATH_PREFIXES"] = "/workspace,/tmp"
        reset_security_config()

    def test_valid_workspace_path(self):
        """Test that workspace paths are allowed."""
        is_valid, error = validate_path("/workspace/myfile.txt")
        assert is_valid is True
        assert error == ""

    def test_valid_tmp_path(self):
        """Test that tmp paths are allowed."""
        is_valid, error = validate_path("/tmp/tempfile")
        assert is_valid is True
        assert error == ""

    def test_valid_relative_path(self):
        """Test that relative paths are allowed."""
        is_valid, error = validate_path("myfile.txt")
        assert is_valid is True
        assert error == ""

    def test_path_traversal_rejected(self):
        """Test that path traversal attempts are rejected."""
        is_valid, error = validate_path("/workspace/../etc/passwd")
        assert is_valid is False
        assert "traversal" in error.lower()

    def test_double_dot_rejected(self):
        """Test that .. is rejected."""
        is_valid, error = validate_path("../../../etc/passwd")
        assert is_valid is False
        assert "traversal" in error.lower()

    def test_null_byte_rejected(self):
        """Test that null bytes are rejected."""
        is_valid, error = validate_path("/workspace/file\x00.txt")
        assert is_valid is False
        assert "null" in error.lower()

    def test_etc_path_rejected(self):
        """Test that /etc paths are rejected."""
        is_valid, error = validate_path("/etc/passwd")
        assert is_valid is False
        assert "outside allowed" in error.lower() or "suspicious" in error.lower()

    def test_proc_path_rejected(self):
        """Test that /proc paths are rejected."""
        is_valid, error = validate_path("/proc/self/environ")
        assert is_valid is False

    def test_root_path_rejected(self):
        """Test that /root paths are rejected."""
        is_valid, error = validate_path("/root/.ssh/id_rsa")
        assert is_valid is False


class TestRateLimiter:
    """Tests for the rate limiter."""

    def test_allows_requests_under_limit(self):
        """Test that requests under the limit are allowed."""
        limiter = RateLimiter()

        for i in range(5):
            allowed, remaining = limiter.is_allowed("client1", 5, 60)
            assert allowed is True
            assert remaining == 5 - i - 1

    def test_blocks_requests_over_limit(self):
        """Test that requests over the limit are blocked."""
        limiter = RateLimiter()

        # Use up the limit
        for _ in range(5):
            limiter.is_allowed("client1", 5, 60)

        # Next request should be blocked
        allowed, remaining = limiter.is_allowed("client1", 5, 60)
        assert allowed is False
        assert remaining == 0

    def test_different_clients_independent(self):
        """Test that different clients have independent limits."""
        limiter = RateLimiter()

        # Use up client1's limit
        for _ in range(5):
            limiter.is_allowed("client1", 5, 60)

        # client2 should still be allowed
        allowed, remaining = limiter.is_allowed("client2", 5, 60)
        assert allowed is True

    def test_window_expiration(self):
        """Test that requests are allowed after window expires."""
        limiter = RateLimiter()

        # Use up the limit with a very short window
        for _ in range(3):
            limiter.is_allowed("client1", 3, 1)

        # Should be blocked
        allowed, _ = limiter.is_allowed("client1", 3, 1)
        assert allowed is False

        # Wait for window to expire
        time.sleep(1.1)

        # Should be allowed again
        allowed, _ = limiter.is_allowed("client1", 3, 1)
        assert allowed is True

    def test_retry_after_calculation(self):
        """Test retry-after header calculation."""
        limiter = RateLimiter()

        # Make a request
        limiter.is_allowed("client1", 5, 10)

        # Retry-after should be close to window size
        retry_after = limiter.get_retry_after("client1", 10)
        assert 0 <= retry_after <= 10


class TestSecurityIntegration:
    """Integration tests for security features."""

    def setup_method(self):
        """Reset config before each test."""
        os.environ["API_KEY_ENABLED"] = "true"
        os.environ["API_KEYS"] = "valid-key"
        reset_security_config()

    def test_api_key_hash_comparison(self):
        """Test that API key comparison uses hashes."""
        config = get_security_config()

        # The stored hash should match the hash of the original key
        key_hash = hashlib.sha256(b"valid-key").hexdigest()
        assert key_hash in config.api_keys

        # A different key should not match
        wrong_hash = hashlib.sha256(b"wrong-key").hexdigest()
        assert wrong_hash not in config.api_keys
