#!/bin/bash

# Vikingbot VKE 一键部署脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Vikingbot VKE 一键部署${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

cd "$PROJECT_ROOT"

# Step 1: Build and push image
echo -e "${GREEN}[1/3]${NC} 构建并推送 Docker 镜像..."
docker build -f deploy/Dockerfile -t vikingbot:latest --platform linux/amd64 .
docker tag vikingbot:latest vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest
docker push vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest

# Step 2: Run deploy script
echo -e "${GREEN}[2/3]${NC} 运行部署脚本..."
uv run python deploy/vke/vke_deploy.py --skip-build --skip-push

# Step 3: Check status
echo -e "${GREEN}[3/3]${NC} 检查部署状态..."
sleep 10
echo ""
echo "Pod 状态:"
kubectl get pods -l app=vikingbot
echo ""
echo "PVC 状态:"
kubectl get pvc -l app=vikingbot 2>/dev/null || kubectl get pvc

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "查看日志命令:"
echo "  kubectl logs -l app=vikingbot -f"
