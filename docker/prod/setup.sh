#!/bin/bash
set -euo pipefail

echo "=== Memodi Server Setup (Ubuntu 24) ==="

# Update system
echo "Updating system..."
apt update && apt upgrade -y

# Install Docker (for Caddy)
echo "Installing Docker..."
curl -fsSL https://get.docker.com | sh

# Install uv (for memodi server)
echo "Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install PostgreSQL 16
echo "Installing PostgreSQL 16..."
apt install -y postgresql-16 postgresql-server-dev-16 \
    build-essential git ca-certificates \
    libreadline-dev zlib1g-dev bison flex

# Install pgvector
echo "Installing pgvector..."
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector
cd /tmp/pgvector && make && make install
rm -rf /tmp/pgvector

# Install Apache AGE
echo "Installing Apache AGE..."
git clone --branch PG16/v1.5.0-rc0 https://github.com/apache/age.git /tmp/age
cd /tmp/age && make && make install
rm -rf /tmp/age

# Cleanup build deps
apt purge -y build-essential postgresql-server-dev-16 bison flex
apt autoremove -y

# Create memodi database and user
echo "Configuring PostgreSQL..."
sudo -u postgres psql <<SQL
CREATE USER memodi WITH PASSWORD 'CHANGE_ME_TEMP';
CREATE DATABASE memodi OWNER memodi;
\c memodi
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
SQL

# Allow Docker containers to reach PostgreSQL
PG_CONF="/etc/postgresql/16/main/postgresql.conf"
PG_HBA="/etc/postgresql/16/main/pg_hba.conf"
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = 'localhost,172.17.0.1'/" "$PG_CONF"
echo "host memodi memodi 172.16.0.0/12 md5" >> "$PG_HBA"
systemctl restart postgresql

# Create memodi user
echo "Creating memodi user..."
useradd -m -s /bin/bash memodi || true
usermod -aG docker memodi

# Install uv for memodi user
su - memodi -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

# Create backup directory
mkdir -p /data/memodi/backups
chown -R memodi:memodi /data/memodi

# Firewall
echo "Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow from 172.16.0.0/12 to 172.17.0.1 port 5432
ufw --force enable

echo ""
echo "=== Setup complete ==="
echo ""
echo "IMPORTANT: Change the PostgreSQL password!"
echo "  sudo -u postgres psql -c \"ALTER USER memodi PASSWORD 'your_secure_password';\""
echo ""
echo "Next steps:"
echo "  1. su - memodi"
echo "  2. git clone https://github.com/iam-oov/memodi.git"
echo "  3. cd memodi && uv sync"
echo "  4. cd docker/prod && cp .env.prod.example .env && nano .env"
echo "  5. exit  # back to root"
echo "  6. cp /home/memodi/memodi/docker/prod/memodi.service /etc/systemd/system/"
echo "  7. systemctl daemon-reload && systemctl enable memodi && systemctl start memodi"
echo "  8. su - memodi && cd memodi/docker/prod && docker compose -f docker-compose.prod.yml up -d"
