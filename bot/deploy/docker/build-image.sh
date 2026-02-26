#!/bin/bash

# Vikingbot 一键打镜像脚本
# 功能：
# 1. 构建 Docker 镜像（支持多架构）
# 2. 支持自定义镜像名称和标签
# 3. 显示构建进度和结果

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 默认配置
IMAGE_NAME=${IMAGE_NAME:-vikingbot}
IMAGE_TAG=${IMAGE_TAG:-latest}
DOCKERFILE=${DOCKERFILE:-deploy/Dockerfile}
NO_CACHE=${NO_CACHE:-false}
# 平台配置：默认本地架构，也可指定 linux/amd64, linux/arm64 等
PLATFORM=${PLATFORM:-}
# 是否使用 buildx 进行多架构构建
MULTI_ARCH=${MULTI_ARCH:-false}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Vikingbot 一键构建镜像${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 1. 检查 Docker 是否安装
echo -e "${GREEN}[1/5]${NC} 检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    echo "请先安装 Docker: https://www.docker.com/get-started"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker 已安装"

# 2. 检查 Dockerfile 是否存在
echo -e "${GREEN}[2/5]${NC} 检查 Dockerfile..."
if [ ! -f "$PROJECT_ROOT/$DOCKERFILE" ]; then
    echo -e "${RED}错误: Dockerfile 不存在${NC}"
    echo "路径: $PROJECT_ROOT/$DOCKERFILE"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Dockerfile 存在"

# 3. 检测架构
echo -e "${GREEN}[3/5]${NC} 检测架构..."
if [ -z "$PLATFORM" ]; then
    # 自动检测本地架构
    if [[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]; then
        PLATFORM="linux/arm64"
    else
        PLATFORM="linux/amd64"
    fi
fi
echo -e "  ${GREEN}✓${NC} 目标平台: ${PLATFORM}"

# 4. 显示构建配置
echo -e "${GREEN}[4/5]${NC} 构建配置:"
echo "  项目根目录: $PROJECT_ROOT"
echo "  Dockerfile: $DOCKERFILE"
echo "  镜像名称: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  目标平台: ${PLATFORM}"
echo "  不使用缓存: ${NO_CACHE}"
echo "  多架构构建: ${MULTI_ARCH}"

# 5. 构建镜像
echo -e "${GREEN}[5/5]${NC} 开始构建镜像..."
echo ""

cd "$PROJECT_ROOT"

BUILD_ARGS=""
if [ "$NO_CACHE" = "true" ]; then
    BUILD_ARGS="--no-cache"
fi

if [ "$MULTI_ARCH" = "true" ]; then
    # 多架构构建：同时构建 amd64 和 arm64
    echo "使用 buildx 进行多架构构建..."
    docker buildx build $BUILD_ARGS \
        -f "$DOCKERFILE" \
        -t "${IMAGE_NAME}:${IMAGE_TAG}" \
        --platform linux/amd64,linux/arm64 \
        --load \
        .
else
    # 单架构构建
    docker build $BUILD_ARGS \
        -f "$DOCKERFILE" \
        -t "${IMAGE_NAME}:${IMAGE_TAG}" \
        --platform "$PLATFORM" \
        .
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  镜像构建成功!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "镜像信息:"
echo "  名称: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  平台: ${PLATFORM}"
echo ""
echo "常用命令:"
echo "  查看镜像:    ${YELLOW}docker images ${IMAGE_NAME}${NC}"
echo "  测试镜像:    ${YELLOW}docker run --rm ${IMAGE_NAME}:${IMAGE_TAG} status${NC}"
echo "  删除镜像:    ${YELLOW}docker rmi ${IMAGE_NAME}:${IMAGE_TAG}${NC}"
echo ""
echo "多架构构建:"
echo "  同时构建 amd64+arm64: ${YELLOW}MULTI_ARCH=true ./deploy/docker/build-image.sh${NC}"
echo "  指定架构构建:     ${YELLOW}PLATFORM=linux/amd64 ./deploy/docker/build-image.sh${NC}"
echo ""
