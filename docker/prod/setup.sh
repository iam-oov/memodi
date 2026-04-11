#!/bin/bash
set -euo pipefail

echo "=== Memodi Server Setup (Ubuntu 24) ==="

# Update system
echo "Updating system..."
apt update && apt upgrade -y

# Install Docker
echo "Installing Docker..."
curl -fsSL https://get.docker.com | sh

# Create memodi user
echo "Creating memodi user..."
useradd -m -s /bin/bash memodi
usermod -aG docker memodi

# Create data directories
echo "Creating data directories..."
mkdir -p /data/memodi/pgdata
mkdir -p /data/memodi/backups
chown -R memodi:memodi /data/memodi

# Firewall
echo "Configuring firewall..."
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps (as memodi user):"
echo "  1. su - memodi"
echo "  2. git clone git@github.com:iam-oov/memodi.git"
echo "  3. cd memodi/docker/prod"
echo "  4. cp .env.prod.example .env"
echo "  5. Edit .env with secure passwords"
echo "  6. docker compose -f docker-compose.prod.yml up -d"
echo ""
echo "Backup cron (as root):"
echo "  crontab -e"
echo "  0 3 * * * cd /home/memodi/memodi/docker/prod && source .env && /home/memodi/memodi/docker/prod/backup.sh >> /data/memodi/backups/cron.log 2>&1"
