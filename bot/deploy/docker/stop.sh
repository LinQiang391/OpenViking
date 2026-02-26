#!/bin/bash

# Vikingbot 停止脚本
# 功能：停止并删除容器

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 默认配置
CONTAINER_NAME=${CONTAINER_NAME:-vikingbot}
REMOVE_IMAGE=${REMOVE_IMAGE:-false}
REMOVE_VOLUME=${REMOVE_VOLUME:-false}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Vikingbot 停止服务${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 1. 检查容器是否存在
echo -e "${GREEN}[1/3]${NC} 检查容器..."
if [ ! "$(docker ps -aq -f name=^/${CONTAINER_NAME}$)" ]; then
    echo -e "  ${YELLOW}容器 ${CONTAINER_NAME} 不存在${NC}"
else
    # 2. 停止容器
    echo -e "${GREEN}[2/3]${NC} 停止容器..."
    if [ "$(docker ps -q -f name=^/${CONTAINER_NAME}$)" ]; then
        echo -e "  停止运行中的容器..."
        docker stop "${CONTAINER_NAME}" > /dev/null
        echo -e "  ${GREEN}✓${NC} 容器已停止"
    fi

    # 3. 删除容器
    echo -e "${GREEN}[3/3]${NC} 删除容器..."
    docker rm "${CONTAINER_NAME}" > /dev/null
    echo -e "  ${GREEN}✓${NC} 容器已删除"
fi

# 可选：删除镜像
if [ "$REMOVE_IMAGE" = "true" ]; then
    echo ""
    echo -e "${YELLOW}删除镜像...${NC}"
    IMAGE_NAME=${IMAGE_NAME:-vikingbot}
    IMAGE_TAG=${IMAGE_TAG:-latest}
    if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${IMAGE_NAME}:${IMAGE_TAG}$"; then
        docker rmi "${IMAGE_NAME}:${IMAGE_TAG}"
        echo -e "  ${GREEN}✓${NC} 镜像已删除"
    fi
fi

# 可选：删除卷
if [ "$REMOVE_VOLUME" = "true" ]; then
    echo ""
    echo -e "${YELLOW}删除数据卷...${NC}"
    VOLUME_NAME="vikingbot_data"
    if docker volume ls -q | grep -q "^${VOLUME_NAME}$"; then
        docker volume rm "${VOLUME_NAME}"
        echo -e "  ${GREEN}✓${NC} 数据卷已删除"
    fi
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  操作完成!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
