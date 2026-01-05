# Security Setup Guide

## Quick Setup for Internet Exposure

### 1. Generate a Strong API Key

```bash
# Generate a secure random API key
openssl rand -hex 32
```

This will output something like: `a1b2c3d4e5f6...` (64 characters)

### 2. Create Your .env File

```bash
cd /home/user/locale2b
cp .env.example .env
nano .env  # or use your preferred editor
```

Edit `.env` and set your API key:

```bash
API_KEY_ENABLED=true
API_KEYS=a1b2c3d4e5f6...  # Your generated key here
```

### 3. Run the Service with Security Enabled

```bash
# Load environment variables and start the service
source .env
export $(cat .env | grep -v '^#' | xargs)
uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080
```

Or use this one-liner:

```bash
env $(cat .env | grep -v '^#' | xargs) uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080
```

### 4. Test the API Key Protection

**Without API key (should fail):**
```bash
curl -X POST http://192.168.0.134:8080/sandboxes \
  -H "Content-Type: application/json" \
  -d '{"memory_mb": 512, "vcpu_count": 1}'
```

Response: `{"detail":"Invalid or missing API key"}`

**With API key (should work):**
```bash
curl -X POST http://192.168.0.134:8080/sandboxes \
  -H "Content-Type: application/json" \
  -H "X-API-Key: a1b2c3d4e5f6..." \
  -d '{"memory_mb": 512, "vcpu_count": 1}'
```

## Running as a Systemd Service

For production, create a systemd service:

### Create Service File

```bash
sudo nano /etc/systemd/system/firecracker-workspace.service
```

**Service file content:**

```ini
[Unit]
Description=Firecracker Workspace Service
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/user/locale2b
EnvironmentFile=/home/user/locale2b/.env
ExecStart=/home/user/locale2b/.venv/bin/uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

### Enable and Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable firecracker-workspace
sudo systemctl start firecracker-workspace
sudo systemctl status firecracker-workspace
```

### View Logs

```bash
sudo journalctl -u firecracker-workspace -f
```

## Security Features

### ✅ API Key Authentication
- Keys are SHA256 hashed in memory (never logged)
- Support for multiple API keys (comma-separated)
- Custom header name support

### ✅ Rate Limiting
- Default: 100 requests per 60 seconds per client
- Tracks by API key or IP address
- Returns `429 Too Many Requests` with `Retry-After` header

### ✅ Path Traversal Protection
- Validates all file paths
- Blocks `../` and suspicious patterns
- Restricts to `/workspace` and `/tmp` by default

### ✅ Request Size Limits
- Default: 10MB maximum
- Prevents denial-of-service attacks

### ✅ Public Endpoints
These endpoints don't require authentication:
- `/health` - Health check
- `/docs` - API documentation
- `/redoc` - Alternative API docs
- `/openapi.json` - OpenAPI schema

## Multiple API Keys

To support multiple clients, use comma-separated keys:

```bash
API_KEYS=key-for-client1,key-for-client2,key-for-client3
```

Each client gets their own key, and you can revoke individual keys by removing them from the list and restarting the service.

## Firewall Configuration

Only expose port 8080:

```bash
# Example: UFW
sudo ufw allow 8080/tcp
sudo ufw enable

# Example: iptables
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
```

## Monitoring

Check rate limit headers in responses:

```bash
curl -I http://192.168.0.134:8080/health
```

Headers:
- `X-RateLimit-Limit: 100`
- `X-RateLimit-Remaining: 99`

## Disable Security (Local Testing Only)

For local development, you can disable authentication:

```bash
API_KEY_ENABLED=false
RATE_LIMIT_ENABLED=false
```

**⚠️ NEVER expose the service to the internet with security disabled!**
