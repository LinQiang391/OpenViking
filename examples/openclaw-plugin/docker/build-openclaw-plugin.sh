#!/usr/bin/env bash
# OpenClaw + OpenViking 插件镜像构建脚本
# 不依赖 OpenViking 源码仓库，从 npm 安装指定版本的 openclaw 和插件
#
# 用法:
#   ./build-openclaw-plugin.sh                                  # 默认: openclaw@latest
#   ./build-openclaw-plugin.sh --openclaw-version 0.1.27        # 指定 openclaw 版本
#   ./build-openclaw-plugin.sh --tag v1.0.0 --push              # 指定镜像 tag 并推送
#   ./build-openclaw-plugin.sh --registry swr.cn-north-4.myhuaweicloud.com/kunpeng-ai
#
# 环境变量:
#   HTTP_PROXY / HTTPS_PROXY   构建期代理（需配合 --network=host）
#   DOCKER_NETWORK             Docker 构建网络模式，默认 host

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

# ── 默认参数 ──
OPENCLAW_VERSION="${OPENCLAW_VERSION:-latest}"
SETUP_HELPER_VERSION="${SETUP_HELPER_VERSION:-latest}"
PLUGIN_REPO="${PLUGIN_REPO:-volcengine/OpenViking}"
PLUGIN_BRANCH="${PLUGIN_BRANCH:-main}"
IMAGE_NAME="${IMAGE_NAME:-openclaw-openviking}"
IMAGE_TAG="${IMAGE_TAG:-}"
REGISTRY="${REGISTRY:-}"
PUSH="${PUSH:-false}"
NO_CACHE="${NO_CACHE:-false}"
DOCKER_NETWORK="${DOCKER_NETWORK:-host}"

usage() {
    cat <<EOF
用法: $(basename "$0") [选项]

选项:
  --openclaw-version VER    OpenClaw 版本 (默认: latest)
  --helper-version VER      插件安装助手版本 (默认: latest)
  --plugin-repo REPO        插件源码仓库 (默认: volcengine/OpenViking)
  --plugin-branch BRANCH    插件源码分支 (默认: main)
  --image-name NAME         镜像名 (默认: openclaw-openviking)
  --tag TAG                 镜像 tag (默认: openclaw 版本号)
  --registry REGISTRY       镜像仓库前缀
  --push                    构建后推送到仓库
  --no-cache                不使用 Docker 缓存
  --network NET             Docker 构建网络模式 (默认: host)
  -h, --help                显示帮助
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --openclaw-version) OPENCLAW_VERSION="$2"; shift 2 ;;
        --helper-version)   SETUP_HELPER_VERSION="$2"; shift 2 ;;
        --plugin-repo)      PLUGIN_REPO="$2"; shift 2 ;;
        --plugin-branch)    PLUGIN_BRANCH="$2"; shift 2 ;;
        --image-name)       IMAGE_NAME="$2"; shift 2 ;;
        --tag)              IMAGE_TAG="$2"; shift 2 ;;
        --registry)         REGISTRY="$2"; shift 2 ;;
        --push)             PUSH=true; shift ;;
        --no-cache)         NO_CACHE=true; shift ;;
        --network)          DOCKER_NETWORK="$2"; shift 2 ;;
        -h|--help)          usage ;;
        *) echo "${RED}未知选项: $1${NC}"; usage ;;
    esac
done

# 如果没有指定 tag，使用 openclaw 版本号
if [[ -z "${IMAGE_TAG}" ]]; then
    if [[ "${OPENCLAW_VERSION}" == "latest" ]]; then
        IMAGE_TAG="latest"
    else
        IMAGE_TAG="${OPENCLAW_VERSION}"
    fi
fi

if [[ -n "${REGISTRY}" ]]; then
    FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
else
    FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  OpenClaw + OpenViking 插件镜像构建${NC}"
echo -e "${BLUE}========================================${NC}"
echo "  OpenClaw 版本:  ${OPENCLAW_VERSION}"
echo "  安装助手版本:   ${SETUP_HELPER_VERSION}"
echo "  插件仓库:       ${PLUGIN_REPO}@${PLUGIN_BRANCH}"
echo "  镜像:           ${FULL_IMAGE}"
echo "  网络模式:       ${DOCKER_NETWORK}"
echo ""

if ! command -v docker &>/dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    exit 1
fi

DOCKERFILE="${SCRIPT_DIR}/Dockerfile.remote"
if [[ ! -f "${DOCKERFILE}" ]]; then
    echo -e "${RED}错误: Dockerfile 不存在: ${DOCKERFILE}${NC}"
    exit 1
fi

# ── 构建参数 ──
BUILD_ARGS=(
    --build-arg "OPENCLAW_VERSION=${OPENCLAW_VERSION}"
    --build-arg "SETUP_HELPER_VERSION=${SETUP_HELPER_VERSION}"
    --build-arg "PLUGIN_REPO=${PLUGIN_REPO}"
    --build-arg "PLUGIN_BRANCH=${PLUGIN_BRANCH}"
    -f "${DOCKERFILE}"
    -t "${FULL_IMAGE}"
)

HAS_BUILDX=false
if docker buildx version &>/dev/null; then
    HAS_BUILDX=true
fi
BUILD_ARGS=(--network "${DOCKER_NETWORK}" "${BUILD_ARGS[@]}")

if [[ -n "${HTTP_PROXY:-}" ]]; then
    BUILD_ARGS+=(--build-arg "HTTP_PROXY=${HTTP_PROXY}")
fi
if [[ -n "${HTTPS_PROXY:-}" ]]; then
    BUILD_ARGS+=(--build-arg "HTTPS_PROXY=${HTTPS_PROXY}")
fi
[[ "${NO_CACHE}" == "true" ]] && BUILD_ARGS+=(--no-cache)

echo -e "${GREEN}开始构建...${NC}"
if [[ "${HAS_BUILDX}" == "true" ]]; then
    [[ "${PUSH}" == "true" ]] && BUILD_ARGS+=(--push) || BUILD_ARGS+=(--load)
    docker buildx build "${BUILD_ARGS[@]}" "${REPO_ROOT}"
else
    echo -e "${YELLOW}  docker buildx 不可用，使用 docker build${NC}"
    docker build "${BUILD_ARGS[@]}" "${REPO_ROOT}"
    if [[ "${PUSH}" == "true" ]]; then
        echo -e "${GREEN}推送镜像: ${FULL_IMAGE}${NC}"
        docker push "${FULL_IMAGE}"
    fi
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  构建完成: ${FULL_IMAGE}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "运行示例:"
echo "  ${YELLOW}docker run -d --name openclaw-plugin \\"
echo "    -e OPENVIKING_BASE_URL=http://your-openviking:1933 \\"
echo "    -p 18789:18789 \\"
echo "    ${FULL_IMAGE}${NC}"
