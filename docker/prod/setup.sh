#!/bin/bash
set -euo pipefail

echo "=== Memodi Server Setup (Ubuntu 24) ==="

# Update system
echo "Updating system..."
apt update && apt upgrade -y

# Install Docker
echo "Installing Docker..."
curl -fsSL https://get.docker.com | sh

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

# Enable extensions in PostgreSQL
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS age;"

# Create memodi database and user
echo "Configuring PostgreSQL..."
sudo -u postgres psql <<SQL
CREATE USER memodi WITH PASSWORD 'CHANGE_ME_TEMP';
CREATE DATABASE memodi OWNER memodi;
\c memodi
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SQL

# Configure PostgreSQL to listen on localhost only
echo "Securing PostgreSQL..."
PG_CONF="/etc/postgresql/16/main/postgresql.conf"
PG_HBA="/etc/postgresql/16/main/pg_hba.conf"
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = 'localhost,172.17.0.1'/" "$PG_CONF"
echo "host memodi memodi 172.16.0.0/12 md5" >> "$PG_HBA"
systemctl restart postgresql

# Create memodi user
echo "Creating memodi user..."
useradd -m -s /bin/bash memodi || true
usermod -aG docker memodi

# Create backup directory
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
echo "IMPORTANT: Change the PostgreSQL password!"
echo "  sudo -u postgres psql -c \"ALTER USER memodi PASSWORD 'your_secure_password';\""
echo ""
echo "Next steps (as memodi user):"
echo "  1. su - memodi"
echo "  2. git clone https://github.com/iam-oov/memodi.git"
echo "  3. cd memodi/docker/prod"
echo "  4. cp .env.prod.example .env"
echo "  5. nano .env  # set passwords and API key"
echo "  6. docker compose -f docker-compose.prod.yml up -d"
echo ""
echo "Backup cron (as root):"
echo "  crontab -e"
echo "  0 3 * * * source /home/memodi/memodi/docker/prod/.env && /home/memodi/memodi/docker/prod/backup.sh >> /data/memodi/backups/cron.log 2>&1"
