#!/bin/bash
set -euo pipefail

# Run this ONCE on the server as root after initial setup
# Migrates from Docker-based memodi-server to native uv + systemd

echo "=== Migrating memodi-server to native ==="

# Stop and remove Docker memodi-server
echo "Stopping Docker memodi-server..."
su - memodi -c "cd memodi/docker/prod && docker compose -f docker-compose.prod.yml down" || true

# Install uv for memodi user
echo "Installing uv for memodi user..."
su - memodi -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

# Install Python dependencies
echo "Installing dependencies..."
su - memodi -c "cd memodi && ~/.local/bin/uv sync"

# Install systemd service
echo "Installing systemd service..."
cp /home/memodi/memodi/docker/prod/memodi.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable memodi
systemctl start memodi

# Allow memodi user to restart the service (for CI/CD)
echo "memodi ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart memodi" > /etc/sudoers.d/memodi
chmod 440 /etc/sudoers.d/memodi

# Restart Caddy (now points to localhost:8787)
su - memodi -c "cd memodi/docker/prod && docker compose -f docker-compose.prod.yml up -d"

echo ""
echo "=== Migration complete ==="
echo "memodi-server: systemd (port 8787)"
echo "caddy: Docker (ports 80, 443)"
echo "postgresql: native (port 5432)"
echo ""
echo "Verify: systemctl status memodi"
echo "Logs:   journalctl -u memodi -f"
