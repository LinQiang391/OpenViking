#!/usr/bin/env bash
# OpenViking 服务镜像构建脚本
# 从源码仓库 clone 指定版本 → 应用 openGauss 补丁 → 编译打镜像
#
# 用法:
#   ./build-openviking.sh                                       # 默认: main 分支
#   ./build-openviking.sh --ov-ref v0.2.9                       # 指定 tag/分支
#   ./build-openviking.sh --ov-ref main --tag latest --push     # 推送到仓库
#   ./build-openviking.sh --repo https://github.com/volcengine/openviking.git
#
# 流程:
#   1. 在临时目录 clone OpenViking 源码 (指定 ref)
#   2. 将 opengauss-minimal.patch 和 Docker 文件拷入
#   3. git apply 补丁
#   4. docker build
#   5. 清理临时目录
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
OV_REPO="${OV_REPO:-https://github.com/volcengine/OpenViking.git}"
OV_REF="${OV_REF:-v0.3.9}"
PATCH_FILE="${PATCH_FILE:-${REPO_ROOT}/opengauss-minimal.patch}"
IMAGE_NAME="${IMAGE_NAME:-openviking}"
IMAGE_TAG="${IMAGE_TAG:-}"
REGISTRY="${REGISTRY:-}"
PUSH="${PUSH:-false}"
NO_CACHE="${NO_CACHE:-false}"
DOCKER_NETWORK="${DOCKER_NETWORK:-host}"
KEEP_BUILD_DIR="${KEEP_BUILD_DIR:-false}"
MODEL_PATH="${MODEL_PATH:-${SCRIPT_DIR}/models/bge-small-zh-v1.5-f16.gguf}"

usage() {
    cat <<EOF
用法: $(basename "$0") [选项]

选项:
  --repo URL                OpenViking 仓库地址
                            (默认: ${OV_REPO})
  --ov-ref REF              分支/tag/commit (默认: main)
  --patch FILE              补丁文件路径 (默认: repo根/opengauss-minimal.patch)
  --model-path FILE         BGE 模型文件路径 (默认: docker/models/bge-small-zh-v1.5-f16.gguf)
                            指定后跳过在线下载，直接 COPY 到镜像
  --image-name NAME         镜像名 (默认: openviking)
  --tag TAG                 镜像 tag (默认: ov-ref 值)
  --registry REGISTRY       镜像仓库前缀
  --push                    构建后推送到仓库
  --no-cache                不使用 Docker 缓存
  --network NET             Docker 构建网络模式 (默认: host)
  --keep-build-dir          构建后不删除临时目录 (调试用)
  -h, --help                显示帮助
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)           OV_REPO="$2"; shift 2 ;;
        --ov-ref)         OV_REF="$2"; shift 2 ;;
        --patch)          PATCH_FILE="$2"; shift 2 ;;
        --image-name)     IMAGE_NAME="$2"; shift 2 ;;
        --tag)            IMAGE_TAG="$2"; shift 2 ;;
        --registry)       REGISTRY="$2"; shift 2 ;;
        --push)           PUSH=true; shift ;;
        --no-cache)       NO_CACHE=true; shift ;;
        --model-path)     MODEL_PATH="$2"; shift 2 ;;
        --network)        DOCKER_NETWORK="$2"; shift 2 ;;
        --keep-build-dir) KEEP_BUILD_DIR=true; shift ;;
        -h|--help)        usage ;;
        *) echo "${RED}未知选项: $1${NC}"; usage ;;
    esac
done

# 将路径转为绝对路径（后续会 cd 到临时目录）
PATCH_FILE="$(cd "$(dirname "${PATCH_FILE}")" && pwd)/$(basename "${PATCH_FILE}")"
if [[ -n "${MODEL_PATH}" && -f "${MODEL_PATH}" ]]; then
    MODEL_PATH="$(cd "$(dirname "${MODEL_PATH}")" && pwd)/$(basename "${MODEL_PATH}")"
fi

# 如果没有指定 tag，使用 ref 名（替换 / 为 -）
if [[ -z "${IMAGE_TAG}" ]]; then
    IMAGE_TAG="${OV_REF//\//-}"
fi

if [[ -n "${REGISTRY}" ]]; then
    FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
else
    FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
fi

# ── 前置检查 ──
if ! command -v docker &>/dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    exit 1
fi
if ! command -v git &>/dev/null; then
    echo -e "${RED}错误: Git 未安装${NC}"
    exit 1
fi
if [[ ! -f "${PATCH_FILE}" ]]; then
    echo -e "${RED}错误: 补丁文件不存在: ${PATCH_FILE}${NC}"
    exit 1
fi

DOCKERFILE_SRC="${SCRIPT_DIR}/Dockerfile.openviking"
ENTRYPOINT_SRC="${SCRIPT_DIR}/entrypoint-openviking.sh"
CONF_TEMPLATE_SRC="${SCRIPT_DIR}/ov.conf.template.json"
CONF_LOCAL_EMBED_SRC="${SCRIPT_DIR}/ov.conf.local-embed.template.json"

for f in "${DOCKERFILE_SRC}" "${ENTRYPOINT_SRC}" "${CONF_TEMPLATE_SRC}" "${CONF_LOCAL_EMBED_SRC}"; do
    if [[ ! -f "${f}" ]]; then
        echo -e "${RED}错误: 必要文件不存在: ${f}${NC}"
        exit 1
    fi
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  OpenViking 服务镜像构建${NC}"
echo -e "${BLUE}========================================${NC}"
echo "  仓库:        ${OV_REPO}"
echo "  分支/Tag:    ${OV_REF}"
echo "  补丁:        ${PATCH_FILE}"
echo "  镜像:        ${FULL_IMAGE}"
echo "  网络模式:    ${DOCKER_NETWORK}"
if [[ -n "${MODEL_PATH}" && -f "${MODEL_PATH}" ]]; then
    echo "  本地模型:    ${MODEL_PATH} ($(du -h "${MODEL_PATH}" | cut -f1))"
else
    echo "  本地模型:    (未指定/不存在，将在线下载)"
fi
echo ""

# ── 步骤 1: 创建临时构建目录 ──
BUILD_DIR="$(mktemp -d /tmp/openviking-build.XXXXXX)"
cleanup() {
    if [[ "${KEEP_BUILD_DIR}" != "true" ]]; then
        echo -e "${BLUE}清理临时构建目录: ${BUILD_DIR}${NC}"
        rm -rf "${BUILD_DIR}"
    else
        echo -e "${YELLOW}保留临时构建目录: ${BUILD_DIR}${NC}"
    fi
}
trap cleanup EXIT

# ── 步骤 2: Clone OpenViking 源码 ──
echo -e "${GREEN}[1/4] 克隆 OpenViking 源码 (${OV_REF})...${NC}"

GIT_CLONE_ARGS=()
if [[ -n "${HTTP_PROXY:-}" ]]; then
    GIT_CLONE_ARGS+=(-c "http.proxy=${HTTP_PROXY}")
fi
if [[ -n "${HTTPS_PROXY:-}" ]]; then
    GIT_CLONE_ARGS+=(-c "https.proxy=${HTTPS_PROXY}")
fi

git "${GIT_CLONE_ARGS[@]}" clone --depth 1 --branch "${OV_REF}" "${OV_REPO}" "${BUILD_DIR}/src" 2>&1 \
    || {
        echo -e "${YELLOW}shallow clone 失败，尝试完整 clone...${NC}"
        git "${GIT_CLONE_ARGS[@]}" clone "${OV_REPO}" "${BUILD_DIR}/src"
        cd "${BUILD_DIR}/src"
        git checkout "${OV_REF}"
    }

# ── 步骤 3: 拷入 Docker 文件并应用补丁 ──
echo -e "${GREEN}[2/4] 拷入 Docker 文件并应用补丁...${NC}"

# 确保目标目录存在
mkdir -p "${BUILD_DIR}/src/examples/openclaw-plugin/docker"

cp "${DOCKERFILE_SRC}" "${BUILD_DIR}/src/examples/openclaw-plugin/docker/Dockerfile.openviking"
cp "${ENTRYPOINT_SRC}" "${BUILD_DIR}/src/examples/openclaw-plugin/docker/entrypoint-openviking.sh"
cp "${CONF_TEMPLATE_SRC}" "${BUILD_DIR}/src/examples/openclaw-plugin/docker/ov.conf.template.json"
cp "${CONF_LOCAL_EMBED_SRC}" "${BUILD_DIR}/src/examples/openclaw-plugin/docker/ov.conf.local-embed.template.json"

# 拷贝本地模型文件到 build context（如果存在）
mkdir -p "${BUILD_DIR}/src/examples/openclaw-plugin/docker/models"
if [[ -n "${MODEL_PATH}" && -f "${MODEL_PATH}" ]]; then
    cp "${MODEL_PATH}" "${BUILD_DIR}/src/examples/openclaw-plugin/docker/models/bge-small-zh-v1.5-f16.gguf"
    echo -e "  ${GREEN}本地模型已拷入 build context: $(basename "${MODEL_PATH}") ($(du -h "${MODEL_PATH}" | cut -f1))${NC}"
else
    echo -e "  ${YELLOW}未找到本地模型文件，构建时将在线下载${NC}"
fi

cd "${BUILD_DIR}/src"
# 应用补丁（--forward 跳过已应用的部分，避免 Docker 文件补丁冲突）
if git apply --check "${PATCH_FILE}" 2>/dev/null; then
    git apply "${PATCH_FILE}"
    echo "  补丁应用成功"
else
    echo -e "${YELLOW}  补丁部分内容可能已存在，尝试跳过已应用的 hunk...${NC}"
    git apply --reject --whitespace=fix "${PATCH_FILE}" 2>&1 || true
    # 清理 .rej 文件
    find . -name '*.rej' -delete 2>/dev/null || true
fi

# ── 步骤 4: 构建 Docker 镜像 ──
echo -e "${GREEN}[3/4] 构建 Docker 镜像...${NC}"

BUILD_ARGS=(
    -f "examples/openclaw-plugin/docker/Dockerfile.openviking"
    -t "${FULL_IMAGE}"
)

# 检测是否支持 buildx
HAS_BUILDX=false
if docker buildx version &>/dev/null; then
    HAS_BUILDX=true
    BUILD_ARGS=(--network "${DOCKER_NETWORK}" "${BUILD_ARGS[@]}")
fi

if [[ -n "${HTTP_PROXY:-}" ]]; then
    BUILD_ARGS+=(--build-arg "HTTP_PROXY=${HTTP_PROXY}")
fi
if [[ -n "${HTTPS_PROXY:-}" ]]; then
    BUILD_ARGS+=(--build-arg "HTTPS_PROXY=${HTTPS_PROXY}")
fi
[[ "${NO_CACHE}" == "true" ]] && BUILD_ARGS+=(--no-cache)

if [[ "${HAS_BUILDX}" == "true" ]]; then
    [[ "${PUSH}" == "true" ]] && BUILD_ARGS+=(--push) || BUILD_ARGS+=(--load)
    docker buildx build "${BUILD_ARGS[@]}" .
else
    echo -e "${YELLOW}  docker buildx 不可用，使用 docker build${NC}"
    docker build "${BUILD_ARGS[@]}" .
    if [[ "${PUSH}" == "true" ]]; then
        echo -e "${GREEN}推送镜像: ${FULL_IMAGE}${NC}"
        docker push "${FULL_IMAGE}"
    fi
fi

# ── 完成 ──
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  [4/4] 构建完成: ${FULL_IMAGE}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "运行示例:"
echo "  ${YELLOW}docker run -d --name openviking-server \\"
echo "    -e OPENVIKING_EMBEDDING_API_KEY=your-key \\"
echo "    -e OPENVIKING_VLM_API_KEY=your-key \\"
echo "    -p 1933:1933 \\"
echo "    ${FULL_IMAGE}${NC}"
