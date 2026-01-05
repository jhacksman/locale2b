## Summary
This PR adds critical dependencies and security documentation for production deployment:

- **Fix FastAPI file upload dependency**: Adds `python-multipart` package required for file upload endpoints
- **Add security setup documentation**: Comprehensive guide for securing the service when exposed to the internet
- **Add example configuration**: `.env.example` with all security configuration options

## Changes

### Dependencies
- Add `python-multipart>=0.0.6` to `pyproject.toml`
  - Fixes: `RuntimeError: Form data requires "python-multipart" to be installed`
  - Required for `/sandboxes/{sandbox_id}/files/upload` endpoint

### Documentation
- **SECURITY_SETUP.md**: Complete security setup guide including:
  - API key generation and configuration
  - Systemd service setup for production
  - Rate limiting configuration
  - Firewall setup recommendations

- **.env.example**: Example environment configuration with:
  - API key authentication settings
  - Rate limiting options
  - Request size limits
  - Path security settings
  - CORS configuration

## Security Features Documented
- ✅ API Key Authentication (SHA256 hashed)
- ✅ Rate Limiting (100 req/min default)
- ✅ Path Traversal Protection
- ✅ Request Size Limits (10MB default)

## Testing
Tested with:
```bash
# Without API key (fails as expected)
curl -X POST http://192.168.0.134:8080/sandboxes -H "Content-Type: application/json" -d '{"memory_mb": 512}'

# With API key (works)
curl -X POST http://192.168.0.134:8080/sandboxes -H "Content-Type: application/json" -H "X-API-Key: xxx" -d '{"memory_mb": 512}'
```

## Deployment Notes
Users should:
1. Generate API key: `openssl rand -hex 32`
2. Copy `.env.example` to `.env` and configure
3. Run with environment variables loaded
4. See SECURITY_SETUP.md for systemd service setup
