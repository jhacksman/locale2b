#!/bin/bash
# Test script to verify the workspace service is working

set -e

SERVICE_URL="${SERVICE_URL:-http://localhost:8080}"

echo "=== Testing Firecracker Workspace Service ==="
echo "Service URL: $SERVICE_URL"
echo ""

# 1. Health check
echo "1. Health check..."
HEALTH=$(curl -s "$SERVICE_URL/health")
echo "   Response: $HEALTH"
echo ""

# 2. Create sandbox
echo "2. Creating sandbox..."
CREATE_RESPONSE=$(curl -s -X POST "$SERVICE_URL/sandboxes" \
    -H "Content-Type: application/json" \
    -d '{"template": "default", "memory_mb": 512, "vcpu_count": 1}')
echo "   Response: $CREATE_RESPONSE"

SANDBOX_ID=$(echo "$CREATE_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['sandbox_id'])")
echo "   Sandbox ID: $SANDBOX_ID"
echo ""

# Wait for sandbox to be ready
echo "   Waiting for sandbox to be ready..."
sleep 3

# 3. Execute command
echo "3. Executing command (uname -a)..."
EXEC_RESPONSE=$(curl -s -X POST "$SERVICE_URL/sandboxes/$SANDBOX_ID/exec" \
    -H "Content-Type: application/json" \
    -d '{"command": "uname -a", "timeout_seconds": 30}')
echo "   Response: $EXEC_RESPONSE"
echo ""

# 4. Write file
echo "4. Writing file..."
WRITE_RESPONSE=$(curl -s -X POST "$SERVICE_URL/sandboxes/$SANDBOX_ID/files/write" \
    -H "Content-Type: application/json" \
    -d '{"path": "/workspace/hello.py", "content": "print(\"Hello from Firecracker!\")\n"}')
echo "   Response: $WRITE_RESPONSE"
echo ""

# 5. Execute Python file
echo "5. Executing Python file..."
PYTHON_RESPONSE=$(curl -s -X POST "$SERVICE_URL/sandboxes/$SANDBOX_ID/exec" \
    -H "Content-Type: application/json" \
    -d '{"command": "python3 /workspace/hello.py", "timeout_seconds": 30}')
echo "   Response: $PYTHON_RESPONSE"
echo ""

# 6. List files
echo "6. Listing workspace files..."
LIST_RESPONSE=$(curl -s "$SERVICE_URL/sandboxes/$SANDBOX_ID/files/list?path=/workspace")
echo "   Response: $LIST_RESPONSE"
echo ""

# 7. Read file
echo "7. Reading file..."
READ_RESPONSE=$(curl -s "$SERVICE_URL/sandboxes/$SANDBOX_ID/files/read?path=/workspace/hello.py")
echo "   Response: $READ_RESPONSE"
echo ""

# 8. Destroy sandbox
echo "8. Destroying sandbox..."
DESTROY_RESPONSE=$(curl -s -X DELETE "$SERVICE_URL/sandboxes/$SANDBOX_ID")
echo "   Response: $DESTROY_RESPONSE"
echo ""

echo "=== All Tests Completed ==="
