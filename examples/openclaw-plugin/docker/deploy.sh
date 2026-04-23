#!/usr/bin/env bash
################################################################################
# OpenClaw + OpenViking + openGauss 一键部署脚本
#
# 用法:
#   bash deploy.sh -password <密码>    — 全量部署 (openGauss + OpenViking + OpenClaw)
#   bash deploy.sh                      — 部署 OpenViking + OpenClaw (无 openGauss)
#   bash deploy.sh --cleanup            — 清理所有容器
#   bash deploy.sh --status             — 查看部署状态
#   bash deploy.sh --restart            — 重启所有容器
################################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/deploy.env"
OV_CONF_DIR=""
trap '[[ -n "$OV_CONF_DIR" && -d "$OV_CONF_DIR" ]] && rm -rf "$OV_CONF_DIR"' EXIT

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}========== $* ==========${NC}"; }

check_command() {
    if ! command -v "$1" &>/dev/null; then
        log_error "未找到命令: $1，请先安装后再运行此脚本"
        exit 1
    fi
}

# ======================== 密码复杂度校验 ========================

validate_password() {
    local pwd="$1"
    local categories=0

    if [[ ${#pwd} -lt 8 ]]; then
        log_error "密码长度不足 8 个字符"
        return 1
    fi

    [[ "$pwd" =~ [A-Z] ]] && (( categories++ ))
    [[ "$pwd" =~ [a-z] ]] && (( categories++ ))
    [[ "$pwd" =~ [0-9] ]] && (( categories++ ))
    [[ "$pwd" =~ [\#\?\!\@\$\%\^\&\*] ]] && (( categories++ ))

    if (( categories < 3 )); then
        log_error "密码复杂度不够：必须包含大写字母、小写字母、数字、特殊符号(#?!@\$%^&*)中的至少三种"
        return 1
    fi

    return 0
}

# ======================== 参数解析 ========================

OG_PASSWORD=""
PARSED_ACTION=""

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -password)
                if [[ -z "${2:-}" ]]; then
                    log_error "-password 参数需要指定密码值"
                    exit 1
                fi
                OG_PASSWORD="$2"
                shift 2
                ;;
            --cleanup|--status|--restart)
                PARSED_ACTION="$1"
                shift
                ;;
            *)
                log_error "未知参数: $1"
                echo "用法: bash deploy.sh [-password <密码>] [--cleanup|--status|--restart]"
                exit 1
                ;;
        esac
    done
}

# ======================== 加载配置 ========================

load_config() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error "配置文件不存在: $ENV_FILE"
        log_error "请先复制并编辑 deploy.env 文件"
        exit 1
    fi

    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a

    # 默认值
    IMAGE_TAG="${IMAGE_TAG:-latest}"
    ENABLE_OPENGAUSS="${ENABLE_OPENGAUSS:-true}"

    # openGauss 镜像
    OG_IMAGE_REPO="${OG_IMAGE_REPO:-swr.cn-north-4.myhuaweicloud.com/kunpeng-ai/opengauss-distributed}"
    OG_IMAGE_TAG="${OG_IMAGE_TAG:-${IMAGE_TAG}}"
    OG_IMAGE="${OG_IMAGE_REPO}:${OG_IMAGE_TAG}"

    # OpenViking 镜像
    OV_IMAGE_REPO="${OV_IMAGE_REPO:-swr.cn-north-4.myhuaweicloud.com/kunpeng-ai/openviking}"
    OV_IMAGE_TAG="${OV_IMAGE_TAG:-${IMAGE_TAG}}"
    OV_IMAGE="${OV_IMAGE_REPO}:${OV_IMAGE_TAG}"

    # OpenClaw 镜像
    OC_IMAGE_REPO="${OC_IMAGE_REPO:-swr.cn-north-4.myhuaweicloud.com/kunpeng-ai/openclaw-openviking}"
    OC_IMAGE_TAG="${OC_IMAGE_TAG:-${IMAGE_TAG}}"
    OC_IMAGE="${OC_IMAGE_REPO}:${OC_IMAGE_TAG}"

    # 容器与端口
    OG_CONTAINER_NAME="${OG_CONTAINER_NAME:-opengauss}"
    OG_HOST_PORT="${OG_HOST_PORT:-15432}"
    OG_USER="${OG_USER:-gaussdb}"
    OG_NODE_NAME="${OG_NODE_NAME:-gaussdb}"
    OG_DB_NAME="${OG_DB_NAME:-omm}"
    OG_PORT="${OG_PORT:-5432}"
    OG_MODE="${OG_MODE:-standalone}"
    OG_CPUSET_CPUS="${OG_CPUSET_CPUS:-}"
    OG_WAIT_TIMEOUT="${OG_WAIT_TIMEOUT:-120}"

    OV_CONTAINER_NAME="${OV_CONTAINER_NAME:-openviking}"
    OV_PORT="${OV_PORT:-1933}"
    OV_HOST_PORT="${OV_HOST_PORT:-${OV_PORT}}"
    HOST_IP="${HOST_IP:-172.17.0.1}"

    OC_CONTAINER_NAME="${OC_CONTAINER_NAME:-openclaw}"
    OC_PORT="${OC_PORT:-18790}"
    OC_HOST_PORT="${OC_HOST_PORT:-${OC_PORT}}"

    DOCKER_NET_MODE="${DOCKER_NET_MODE:-bridge}"
    DOCKER_NETWORK_NAME="${DOCKER_NETWORK_NAME:-openviking-net}"

    OPENVIKING_ROOT_API_KEY="${OPENVIKING_ROOT_API_KEY:-}"
    OPENVIKING_TARGET_URI="${OPENVIKING_TARGET_URI:-viking://user/memories}"
    OPENVIKING_AGENT_ID="${OPENVIKING_AGENT_ID:-main}"
    OPENCLAW_DATA_DIR="${OPENCLAW_DATA_DIR:-}"
    OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-}"
    SKIP_PULL="${SKIP_PULL:-false}"
    AUTO_HEALTH_CHECK="${AUTO_HEALTH_CHECK:-true}"

    # 必填项检查
    if [[ -z "${OPENVIKING_ROOT_API_KEY}" || "${OPENVIKING_ROOT_API_KEY}" == your_* ]]; then
        log_error "必填配置项 OPENVIKING_ROOT_API_KEY 未设置或仍为模板值，请在 deploy.env 中修改"
        exit 1
    fi

    log_info "openGauss 镜像:  ${OG_IMAGE}"
    log_info "OpenViking 镜像: ${OV_IMAGE}"
    log_info "OpenClaw 镜像:   ${OC_IMAGE}"
}

# ======================== 前置检查 ========================

preflight_check() {
    log_step "前置检查"
    check_command docker

    if ! docker info &>/dev/null; then
        log_error "Docker 服务未运行或当前用户无权限"
        exit 1
    fi
    log_info "Docker 环境正常"
}

# ======================== 镜像拉取 ========================

pull_images() {
    if [[ "${SKIP_PULL}" == "true" ]]; then
        log_info "已跳过镜像拉取（SKIP_PULL=true）"
        return
    fi

    log_step "检查 Docker 镜像"

    if [[ "${ENABLE_OPENGAUSS}" == "true" ]]; then
        if docker image inspect "${OG_IMAGE}" &>/dev/null; then
            log_info "openGauss 镜像已存在: ${OG_IMAGE}"
        else
            log_info "拉取 openGauss 镜像: ${OG_IMAGE}"
            docker pull "${OG_IMAGE}"
        fi
    fi

    if docker image inspect "${OV_IMAGE}" &>/dev/null; then
        log_info "OpenViking 镜像已存在: ${OV_IMAGE}"
    else
        log_info "拉取 OpenViking 镜像: ${OV_IMAGE}"
        docker pull "${OV_IMAGE}"
    fi

    if docker image inspect "${OC_IMAGE}" &>/dev/null; then
        log_info "OpenClaw 镜像已存在: ${OC_IMAGE}"
    else
        log_info "拉取 OpenClaw 镜像: ${OC_IMAGE}"
        docker pull "${OC_IMAGE}"
    fi
}

# ======================== Docker 网络 ========================

setup_network() {
    if [[ "${DOCKER_NET_MODE}" == "host" ]]; then
        return
    fi
    if ! docker network inspect "${DOCKER_NETWORK_NAME}" &>/dev/null; then
        docker network create "${DOCKER_NETWORK_NAME}" >/dev/null
        log_info "创建 Docker 网络: ${DOCKER_NETWORK_NAME}"
    else
        log_info "Docker 网络已存在: ${DOCKER_NETWORK_NAME}"
    fi
}

# ======================== 容器管理辅助 ========================

ensure_container() {
    local name="$1"
    shift
    local -a run_args=("$@")

    if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
        log_info "容器 ${name} 已在运行"
        return
    fi

    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        log_warn "容器 ${name} 已存在但已停止，尝试启动..."
        docker start "${name}"
        return
    fi

    docker run "${run_args[@]}"
}

# ======================== openGauss 部署 ========================

deploy_opengauss() {
    log_step "部署 openGauss 数据库"

    local -a args=(
        --name "${OG_CONTAINER_NAME}" --privileged=true -d
        --restart unless-stopped
        -e "GS_PASSWORD=${OG_PASSWORD}"
        -p "${OG_HOST_PORT}:${OG_PORT}"
    )

    if [[ "${DOCKER_NET_MODE}" != "host" ]]; then
        args+=(--network "${DOCKER_NETWORK_NAME}")
    fi

    [[ "${OG_USER}" != "gaussdb" ]]     && args+=(-e "GS_USERNAME=${OG_USER}")
    [[ "${OG_NODE_NAME}" != "gaussdb" ]] && args+=(-e "GS_NODENAME=${OG_NODE_NAME}")
    [[ "${OG_PORT}" != "5432" ]]         && args+=(-e "GS_PORT=${OG_PORT}")
    [[ -n "${OG_CPUSET_CPUS}" ]]         && args+=(--cpuset-cpus="${OG_CPUSET_CPUS}")

    args+=("${OG_IMAGE}")

    ensure_container "${OG_CONTAINER_NAME}" "${args[@]}"
    log_info "openGauss 容器已启动"
}

wait_opengauss_ready() {
    log_info "等待 openGauss 就绪（最多 ${OG_WAIT_TIMEOUT} 秒）..."
    local elapsed=0
    local interval=3

    while (( elapsed < OG_WAIT_TIMEOUT )); do
        # 兼容 opengauss-server / opengauss-distributed / kunpeng 镜像
        if docker exec "${OG_CONTAINER_NAME}" su - omm -c "gsql -d postgres -p ${OG_PORT} -c 'SELECT 1;'" &>/dev/null 2>&1 \
        || docker exec "${OG_CONTAINER_NAME}" su - omm -c "gsql -d omm -p ${OG_PORT} -c 'SELECT 1;'" &>/dev/null 2>&1 \
        || docker exec "${OG_CONTAINER_NAME}" bash -c "export LD_LIBRARY_PATH=/usr/local/opengauss/lib:\$LD_LIBRARY_PATH && /usr/local/opengauss/bin/gsql -d omm -p ${OG_PORT} -U ${OG_USER} -W '${OG_PASSWORD}' -c 'SELECT 1;'" &>/dev/null 2>&1; then
            log_info "openGauss 已就绪（耗时 ${elapsed} 秒）"
            init_opengauss_db
            return 0
        fi
        if (( elapsed > 0 && elapsed % 15 == 0 )); then
            log_warn "已等待 ${elapsed} 秒，数据库仍未就绪..."
        fi
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
    done

    log_error "openGauss 在 ${OG_WAIT_TIMEOUT} 秒内未就绪"
    log_error "请检查: docker logs ${OG_CONTAINER_NAME}"
    exit 1
}

init_opengauss_db() {
    # omm 是默认数据库，无需创建
    if [[ "${OG_DB_NAME}" == "omm" || "${OG_DB_NAME}" == "postgres" ]]; then
        log_info "使用默认数据库: ${OG_DB_NAME}（跳过创建）"
        return
    fi

    log_info "初始化 openGauss 数据库: ${OG_DB_NAME}"

    local gsql_db="postgres"
    if ! docker exec "${OG_CONTAINER_NAME}" su - omm -c "gsql -d postgres -p ${OG_PORT} -c 'SELECT 1;'" &>/dev/null 2>&1; then
        gsql_db="omm"
    fi

    if docker exec "${OG_CONTAINER_NAME}" su - omm -c \
        "gsql -d ${gsql_db} -p ${OG_PORT} -c \"SELECT 1 FROM pg_database WHERE datname='${OG_DB_NAME}';\"" 2>/dev/null \
        | grep -q "1 row"; then
        log_info "数据库 ${OG_DB_NAME} 已存在"
        return
    fi

    docker exec "${OG_CONTAINER_NAME}" su - omm -c \
        "gsql -d ${gsql_db} -p ${OG_PORT} -c \"CREATE DATABASE ${OG_DB_NAME} ENCODING 'UTF8';\""

    if [[ "${OG_USER}" != "omm" ]]; then
        docker exec "${OG_CONTAINER_NAME}" su - omm -c \
            "gsql -d ${gsql_db} -p ${OG_PORT} -c \"GRANT ALL PRIVILEGES ON DATABASE ${OG_DB_NAME} TO ${OG_USER};\""
    fi
    log_info "数据库 ${OG_DB_NAME} 创建完成"
}

# ======================== OpenViking 部署 ========================

generate_ov_conf() {
    OV_CONF_DIR="$(mktemp -d)"
    local conf_path="${OV_CONF_DIR}/ov.conf"

    local root_key_json="null"
    [[ -n "${OPENVIKING_ROOT_API_KEY}" ]] && root_key_json="\"${OPENVIKING_ROOT_API_KEY}\""

    # storage 部分: bridge 模式用容器名访问 openGauss，host 模式用 127.0.0.1
    local og_connect_host og_connect_port
    if [[ "${DOCKER_NET_MODE}" == "host" ]]; then
        og_connect_host="127.0.0.1"
        og_connect_port="${OG_HOST_PORT}"
    else
        og_connect_host="${OG_CONTAINER_NAME}"
        og_connect_port="${OG_PORT}"
    fi

    local storage_json=""
    if [[ "${ENABLE_OPENGAUSS}" == "true" ]]; then
        storage_json=$(cat <<EOSTORAGE
    "storage": {
        "workspace": "/root/.openviking/data",
        "vectordb": {
            "backend": "opengauss",
            "dimension": ${OPENVIKING_EMBEDDING_DIMENSION:-512},
            "opengauss": {
                "host": "${og_connect_host}",
                "port": ${og_connect_port},
                "user": "${OG_USER}",
                "password": "${OG_PASSWORD}",
                "db_name": "${OG_DB_NAME}",
                "mode": "${OG_MODE}"
            }
        }
    }
EOSTORAGE
)
    else
        storage_json=$(cat <<EOSTORAGE
    "storage": {
        "workspace": "/root/.openviking/data",
        "vectordb": {
            "backend": "local",
            "dimension": ${OPENVIKING_EMBEDDING_DIMENSION:-512}
        }
    }
EOSTORAGE
)
    fi

    # embedding 部分
    local embedding_json=""
    if [[ -n "${OPENVIKING_EMBEDDING_API_KEY:-}" ]]; then
        embedding_json=$(cat <<EOEMBED
    "embedding": {
        "dense": {
            "provider": "${OPENVIKING_EMBEDDING_PROVIDER:-volcengine}",
            "api_key": "${OPENVIKING_EMBEDDING_API_KEY}",
            "model": "${OPENVIKING_EMBEDDING_MODEL:-doubao-embedding-vision-250615}",
            "api_base": "${OPENVIKING_EMBEDDING_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}",
            "dimension": ${OPENVIKING_EMBEDDING_DIMENSION:-1024},
            "input": "multimodal"
        }
    },
EOEMBED
)
    fi

    # vlm 部分
    local vlm_json=""
    if [[ -n "${OPENVIKING_VLM_API_KEY:-}" ]]; then
        vlm_json=$(cat <<EOVLM
    "vlm": {
        "provider": "${OPENVIKING_VLM_PROVIDER:-volcengine}",
        "api_key": "${OPENVIKING_VLM_API_KEY}",
        "model": "${OPENVIKING_VLM_MODEL:-doubao-seed-1-8-251228}",
        "api_base": "${OPENVIKING_VLM_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}",
        "temperature": 0.1,
        "max_retries": 3
    },
EOVLM
)
    fi

    cat > "${conf_path}" <<EOCONF
{
    "server": {
        "host": "0.0.0.0",
        "port": ${OV_PORT},
        "root_api_key": ${root_key_json},
        "cors_origins": ["*"]
    },
${storage_json},
${embedding_json}
${vlm_json}
    "auto_generate_l0": false,
    "auto_generate_l1": false
}
EOCONF

    # 用 python 清理 JSON（移除多余逗号等）
    python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    txt = f.read()
conf = json.loads(txt)
with open(sys.argv[1], 'w') as f:
    json.dump(conf, f, indent=2)
" "${conf_path}" 2>/dev/null || true

    echo "${conf_path}"
}

deploy_openviking() {
    log_step "部署 OpenViking 服务"

    local -a args=(
        --name "${OV_CONTAINER_NAME}" -d
        --restart unless-stopped
        -e "OPENVIKING_SERVER_PORT=${OV_PORT}"
        -e "OPENVIKING_REGENERATE_CONFIG=0"
    )

    if [[ "${DOCKER_NET_MODE}" == "host" ]]; then
        args+=(--network host)
    else
        args+=(--network "${DOCKER_NETWORK_NAME}" -p "${OV_HOST_PORT}:${OV_PORT}")
    fi

    # 持久化
    if [[ -n "${OPENVIKING_DATA_DIR}" ]]; then
        mkdir -p "${OPENVIKING_DATA_DIR}"
        args+=(-v "${OPENVIKING_DATA_DIR}:/root/.openviking/data")
    fi

    args+=("${OV_IMAGE}")

    # 先启动容器（用默认配置）
    local need_restart="false"
    if docker ps --format '{{.Names}}' | grep -q "^${OV_CONTAINER_NAME}$"; then
        log_info "容器 ${OV_CONTAINER_NAME} 已在运行"
    elif docker ps -a --format '{{.Names}}' | grep -q "^${OV_CONTAINER_NAME}$"; then
        docker start "${OV_CONTAINER_NAME}" >/dev/null
    else
        docker run "${args[@]}" >/dev/null
        need_restart="true"
    fi

    # 等容器启动
    sleep 3

    # 生成 ov.conf 并 docker cp 进去
    local ov_conf
    ov_conf="$(generate_ov_conf)"
    log_info "生成 ov.conf → docker cp 到容器"
    docker cp "${ov_conf}" "${OV_CONTAINER_NAME}:/root/.openviking/ov.conf"

    if [[ "${need_restart}" == "true" ]]; then
        docker restart "${OV_CONTAINER_NAME}" >/dev/null
        log_info "OpenViking 已重启（应用新配置）"
    fi

    log_info "OpenViking 容器已启动（${DOCKER_NET_MODE} 网络，端口 ${OV_PORT}）"
    if [[ "${ENABLE_OPENGAUSS}" == "true" ]]; then
        log_info "VectorDB: opengauss @ ${HOST_IP}:${OG_HOST_PORT}/${OG_DB_NAME}"
    else
        log_info "VectorDB: local"
    fi
}

wait_openviking_ready() {
    log_info "等待 OpenViking 服务就绪..."
    local elapsed=0
    local max_wait=60
    local interval=3

    local health_url="http://${HOST_IP}:${OV_HOST_PORT}/health"

    while (( elapsed < max_wait )); do
        if curl --noproxy '*' -sf "${health_url}" &>/dev/null; then
            log_info "OpenViking 服务已就绪（耗时 ${elapsed} 秒）"
            return 0
        fi
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
        if (( elapsed > 0 && elapsed % 15 == 0 )); then
            log_warn "已等待 ${elapsed} 秒，OpenViking 仍未就绪..."
        fi
    done

    log_error "OpenViking 在 ${max_wait} 秒内未就绪"
    log_error "请检查: docker logs ${OV_CONTAINER_NAME}"
    exit 1
}

# ======================== OpenClaw 部署 ========================

deploy_openclaw() {
    log_step "部署 OpenClaw 网关"

    local ov_url
    if [[ "${DOCKER_NET_MODE}" == "host" ]]; then
        ov_url="http://127.0.0.1:${OV_PORT}"
    else
        ov_url="http://${OV_CONTAINER_NAME}:${OV_PORT}"
    fi

    local -a args=(
        --name "${OC_CONTAINER_NAME}" -d
        --restart unless-stopped
        -e "OPENVIKING_BASE_URL=${ov_url}"
        -e "OPENVIKING_API_KEY=${OPENVIKING_ROOT_API_KEY}"
        -e "OPENVIKING_ACCOUNT_ID=${OV_ACCOUNT_ID:-default}"
        -e "OPENVIKING_USER_ID=${OV_USER_ID:-default}"
        -e "OPENVIKING_AGENT_ID=${OPENVIKING_AGENT_ID}"
        -e "OPENVIKING_TARGET_URI=${OPENVIKING_TARGET_URI}"
        -e "OPENCLAW_GATEWAY_PORT=${OC_PORT}"
    )

    if [[ "${DOCKER_NET_MODE}" == "host" ]]; then
        args+=(--network host)
    else
        args+=(--network "${DOCKER_NETWORK_NAME}" -p "${OC_HOST_PORT}:${OC_PORT}")
    fi

    # LLM 模型配置（entrypoint 自动处理：模型名 → provider 推断 → openclaw.json + auth-profiles.json）
    [[ -n "${OPENCLAW_DEFAULT_MODEL:-}" ]]  && args+=(-e "OPENCLAW_DEFAULT_MODEL=${OPENCLAW_DEFAULT_MODEL}")
    [[ -n "${OPENCLAW_LLM_API_KEY:-}" ]]    && args+=(-e "OPENCLAW_LLM_API_KEY=${OPENCLAW_LLM_API_KEY}")
    [[ -n "${OPENCLAW_LLM_API_BASE:-}" ]]   && args+=(-e "OPENCLAW_LLM_API_BASE=${OPENCLAW_LLM_API_BASE}")
    # provider 专用 key（向后兼容）
    [[ -n "${VOLCANO_ENGINE_API_KEY:-}" ]]  && args+=(-e "VOLCANO_ENGINE_API_KEY=${VOLCANO_ENGINE_API_KEY}")
    [[ -n "${OPENAI_API_KEY:-}" ]]          && args+=(-e "OPENAI_API_KEY=${OPENAI_API_KEY}")
    [[ -n "${ANTHROPIC_API_KEY:-}" ]]       && args+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
    [[ -n "${ZAI_API_KEY:-}" ]]             && args+=(-e "ZAI_API_KEY=${ZAI_API_KEY}")

    # 挂载本地 entrypoint（如果存在，支持不重新打镜像即生效）
    local local_entrypoint="${SCRIPT_DIR}/entrypoint-remote.sh"
    if [[ -f "${local_entrypoint}" ]]; then
        args+=(-v "${local_entrypoint}:/usr/local/bin/entrypoint.sh:ro")
    fi

    # 持久化
    if [[ -n "${OPENCLAW_DATA_DIR:-}" ]]; then
        mkdir -p "${OPENCLAW_DATA_DIR}"
        args+=(-v "${OPENCLAW_DATA_DIR}:/root/.openclaw")
    fi

    args+=("${OC_IMAGE}")

    # 每次部署都重新 bootstrap 配置
    args+=(-e "OPENCLAW_REAPPLY_CONFIG=1")

    ensure_container "${OC_CONTAINER_NAME}" "${args[@]}"
    log_info "OpenClaw 容器已启动"
}

# ======================== 健康检查 ========================

health_check() {
    if [[ "${AUTO_HEALTH_CHECK}" != "true" ]]; then
        return
    fi

    log_step "服务健康检查"

    log_info "等待 OpenViking 服务就绪..."
    local elapsed=0
    local max_wait=60
    local interval=3

    while (( elapsed < max_wait )); do
        if curl --noproxy '*' -sf "http://${HOST_IP}:${OV_HOST_PORT}/health" &>/dev/null; then
            log_info "OpenViking 健康检查通过 ✓"
            break
        fi
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
    done

    if (( elapsed >= max_wait )); then
        log_warn "OpenViking 健康检查未在 ${max_wait} 秒内通过"
        log_warn "  curl http://${HOST_IP}:${OV_HOST_PORT}/health"
        log_warn "  docker logs ${OV_CONTAINER_NAME}"
    fi

    sleep 3

    log_info "检查 OpenClaw 网关..."
    if curl --noproxy '*' -sf "http://${HOST_IP}:${OC_HOST_PORT}" &>/dev/null 2>&1; then
        log_info "OpenClaw 网关响应正常 ✓"
    else
        log_warn "OpenClaw 网关暂未响应（可能仍在启动中）"
        log_warn "  docker logs ${OC_CONTAINER_NAME}"
    fi

    echo ""
    log_info "容器运行状态:"
    docker ps \
        --filter "name=${OG_CONTAINER_NAME}" \
        --filter "name=${OV_CONTAINER_NAME}" \
        --filter "name=${OC_CONTAINER_NAME}" \
        --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

# ======================== 清理 ========================

cleanup() {
    log_step "清理部署的容器"

    for name in "${OC_CONTAINER_NAME}" "${OV_CONTAINER_NAME}" "${OG_CONTAINER_NAME}"; do
        if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            log_info "停止并删除容器: ${name}"
            docker rm -f "${name}"
        else
            log_info "容器不存在: ${name}"
        fi
    done

    log_info "清理完成"
}

# ======================== 状态查看 ========================

show_status() {
    log_step "部署状态"

    for name in "${OG_CONTAINER_NAME}" "${OV_CONTAINER_NAME}" "${OC_CONTAINER_NAME}"; do
        if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
            log_info "${name}: 运行中"
        elif docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            log_warn "${name}: 已停止"
        else
            log_error "${name}: 不存在"
        fi
    done

    echo ""
    docker ps \
        --filter "name=${OG_CONTAINER_NAME}" \
        --filter "name=${OV_CONTAINER_NAME}" \
        --filter "name=${OC_CONTAINER_NAME}" \
        --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true

    if command -v curl &>/dev/null; then
        echo ""
        log_info "OpenViking 健康检查:"
        if curl --noproxy '*' -sf "http://${HOST_IP}:${OV_HOST_PORT}/health" 2>/dev/null; then
            echo ""
        else
            log_warn "服务未响应"
        fi
    fi
}

# ======================== 重启 ========================

restart_all() {
    log_step "重启所有容器"

    for name in "${OC_CONTAINER_NAME}" "${OV_CONTAINER_NAME}" "${OG_CONTAINER_NAME}"; do
        if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            log_info "重启容器: ${name}"
            docker restart "${name}"
        fi
    done

    log_info "重启完成"
    health_check
}

# ======================== 部署完成提示 ========================

print_summary() {
    log_step "部署完成"
    echo ""
    echo "  服务信息:"
    if [[ "${ENABLE_OPENGAUSS}" == "true" ]]; then
        echo "    openGauss:  ${HOST_IP}:${OG_HOST_PORT} (user=${OG_USER}, db=${OG_DB_NAME})"
    fi
    echo "    OpenViking: http://${HOST_IP}:${OV_HOST_PORT}"
    echo "    OpenClaw:   http://${HOST_IP}:${OC_HOST_PORT}"
    echo ""
    echo "  常用命令:"
    echo "    查看状态:   bash ${SCRIPT_DIR}/deploy.sh --status"
    echo "    重启服务:   bash ${SCRIPT_DIR}/deploy.sh --restart"
    echo "    清理容器:   bash ${SCRIPT_DIR}/deploy.sh --cleanup"
    echo "    健康检查:   curl http://${HOST_IP}:${OV_HOST_PORT}/health"
    echo "    查看日志:"
    if [[ "${ENABLE_OPENGAUSS}" == "true" ]]; then
        echo "      docker logs ${OG_CONTAINER_NAME}"
    fi
    echo "      docker logs ${OV_CONTAINER_NAME}"
    echo "      docker logs ${OC_CONTAINER_NAME}"
    echo ""
}

# ======================== 主流程 ========================

main() {
    parse_args "$@"

    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║      OpenClaw + OpenViking + openGauss 一键部署脚本            ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    load_config
    preflight_check

    case "${PARSED_ACTION}" in
        --cleanup)
            cleanup
            return
            ;;
        --status)
            show_status
            return
            ;;
        --restart)
            restart_all
            return
            ;;
        "")
            if [[ "${ENABLE_OPENGAUSS}" == "true" ]]; then
                if [[ -z "${OG_PASSWORD}" ]]; then
                    log_error "启用 openGauss 时必须通过 -password 参数指定数据库密码"
                    echo ""
                    echo "用法: bash deploy.sh -password <密码>"
                    echo ""
                    echo "密码要求："
                    echo "  - 长度至少 8 个字符"
                    echo "  - 包含大写字母、小写字母、数字、特殊符号(#?!@\$%^&*)中的至少三种"
                    exit 1
                fi
                if ! validate_password "${OG_PASSWORD}"; then
                    exit 1
                fi
                log_info "密码复杂度校验通过"
                log_info "部署模式: openGauss + OpenViking + OpenClaw"
                log_info "网络模式: ${DOCKER_NET_MODE}"

                pull_images
                setup_network
                deploy_opengauss
                wait_opengauss_ready
                deploy_openviking
                wait_openviking_ready
                deploy_openclaw
                health_check
                print_summary
            else
                log_info "部署模式: OpenViking + OpenClaw（无 openGauss）"
                log_info "网络模式: ${DOCKER_NET_MODE}"

                pull_images
                setup_network
                deploy_openviking
                wait_openviking_ready
                deploy_openclaw
                health_check
                print_summary
            fi
            ;;
    esac
}

main "$@"
