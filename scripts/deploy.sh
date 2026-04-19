#!/usr/bin/env bash
# ============================================================
# OI Bot — Deploy / Update script
# Chạy mỗi khi muốn update code mới lên VPS
#
# Usage (từ VPS):
#   cd /opt/oi_bot && bash scripts/deploy.sh
# ============================================================
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " OI Bot — Deploy  $(date '+%Y-%m-%d %H:%M:%S')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "$APP_DIR"

# 1. Pull latest code
echo "[1/4] Pulling latest code..."
git pull origin master

# 2. Kiểm tra .env
if [ ! -f "backend/config/.env" ]; then
    echo "❌ backend/config/.env not found!"
    echo "   Chạy: cp backend/config/.env.example backend/config/.env"
    echo "   Rồi điền API keys vào .env"
    exit 1
fi

# 3. Build images
echo "[2/4] Building Docker images..."
$COMPOSE build --no-cache

# 4. Restart services
echo "[3/4] Restarting services..."
$COMPOSE down
$COMPOSE up -d

# 5. Health check
echo "[4/4] Waiting for backend to be ready..."
sleep 10
if curl -sf http://localhost/api/status > /dev/null; then
    echo "  ✅ Backend healthy!"
else
    echo "  ⚠️  Backend chưa sẵn sàng — kiểm tra logs:"
    echo "     docker logs oi_bot_backend --tail=50"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  SERVICES:"
$COMPOSE ps
echo ""
echo "  Dashboard: http://$(curl -s ifconfig.me 2>/dev/null || echo 'VPS_IP')"
echo "  Logs:      docker logs oi_bot_backend -f"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
