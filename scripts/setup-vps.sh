#!/usr/bin/env bash
# ============================================================
# OI Bot — One-time VPS setup script
# Chạy 1 lần trên Ubuntu 24.04 LTS droplet mới
#
# Usage:
#   ssh root@<VPS_IP>
#   curl -fsSL https://raw.githubusercontent.com/cryptophernolan/lanalaoshi/master/scripts/setup-vps.sh | bash
# ============================================================
set -euo pipefail
REPO="https://github.com/cryptophernolan/lanalaoshi.git"
APP_DIR="/opt/oi_bot"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " OI Divergence Bot — VPS Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. System update
echo "[1/6] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# 2. Install Docker
echo "[2/6] Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
else
    echo "  Docker already installed: $(docker --version)"
fi

# 3. Install git
apt-get install -y -qq git

# 4. Clone repo
echo "[3/6] Cloning repo to $APP_DIR ..."
if [ -d "$APP_DIR" ]; then
    echo "  Directory exists — pulling latest..."
    cd "$APP_DIR" && git pull
else
    git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# 5. Firewall (UFW)
echo "[4/6] Configuring firewall..."
apt-get install -y -qq ufw
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   comment "SSH"
ufw allow 80/tcp   comment "Dashboard + API (nginx)"
ufw --force enable
echo "  UFW rules:"
ufw status numbered

# 6. Create .env nếu chưa có
echo "[5/6] Setting up .env ..."
if [ ! -f "$APP_DIR/backend/config/.env" ]; then
    cp "$APP_DIR/backend/config/.env.example" "$APP_DIR/backend/config/.env"
    echo ""
    echo "  ⚠️  .env được tạo từ .env.example"
    echo "  ➡  Hãy điền API keys vào: $APP_DIR/backend/config/.env"
    echo "  ➡  nano $APP_DIR/backend/config/.env"
else
    echo "  .env đã tồn tại, giữ nguyên"
fi

# 7. Done
echo ""
echo "[6/6] Setup hoàn tất!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  BƯỚC TIẾP THEO:"
echo ""
echo "  1. Điền API keys vào .env:"
echo "     nano $APP_DIR/backend/config/.env"
echo ""
echo "  2. Deploy bot:"
echo "     cd $APP_DIR && bash scripts/deploy.sh"
echo ""
echo "  3. Truy cập dashboard:"
echo "     http://$(curl -s ifconfig.me)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
