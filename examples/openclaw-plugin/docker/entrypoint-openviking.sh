#!/usr/bin/env bash
set -euo pipefail

OPENVIKING_DIR="${OPENVIKING_HOME:-/root/.openviking}"
OV_CONF_PATH="${OPENVIKING_CONFIG_FILE:-${OPENVIKING_DIR}/ov.conf}"
OPENVIKING_WORKSPACE="${OPENVIKING_DIR}/data"

# ========== 用户可配置的环境变量 ==========
# Server
OPENVIKING_SERVER_HOST="${OPENVIKING_SERVER_HOST:-0.0.0.0}"
OPENVIKING_SERVER_PORT="${OPENVIKING_SERVER_PORT:-1933}"
OPENVIKING_ROOT_API_KEY="${OPENVIKING_ROOT_API_KEY:-}"

# Embedding (可选 — 不配置则自动使用 local embedding: bge-small-zh-v1.5-f16)
OPENVIKING_EMBEDDING_PROVIDER="${OPENVIKING_EMBEDDING_PROVIDER:-}"
OPENVIKING_EMBEDDING_API_KEY="${OPENVIKING_EMBEDDING_API_KEY:-}"
OPENVIKING_EMBEDDING_API_BASE="${OPENVIKING_EMBEDDING_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
OPENVIKING_EMBEDDING_MODEL="${OPENVIKING_EMBEDDING_MODEL:-doubao-embedding-vision-250615}"

# VLM (可选 — 不配置则不启用 VLM 功能)
OPENVIKING_VLM_PROVIDER="${OPENVIKING_VLM_PROVIDER:-volcengine}"
OPENVIKING_VLM_API_KEY="${OPENVIKING_VLM_API_KEY:-}"
OPENVIKING_VLM_API_BASE="${OPENVIKING_VLM_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
OPENVIKING_VLM_MODEL="${OPENVIKING_VLM_MODEL:-doubao-seed-1-8-251228}"

# 是否强制重新生成配置
OPENVIKING_REGENERATE_CONFIG="${OPENVIKING_REGENERATE_CONFIG:-0}"

# ========== VectorDB 配置 ==========
OPENVIKING_VECTORDB_BACKEND="${OPENVIKING_VECTORDB_BACKEND:-local}"

# openGauss 连接参数 (仅当 OPENVIKING_VECTORDB_BACKEND=opengauss 时生效)
OG_HOST="${OG_HOST:-127.0.0.1}"
OG_PORT="${OG_PORT:-5432}"
OG_USER="${OG_USER:-gaussdb}"
OG_PASSWORD="${OG_PASSWORD:-}"
OG_DB_NAME="${OG_DB_NAME:-openviking}"
OG_MODE="${OG_MODE:-standalone}"

# ========== 内部默认值 ==========
OPENVIKING_EMBEDDING_DIMENSION="1024"
OPENVIKING_EMBEDDING_INPUT="multimodal"
OPENVIKING_VLM_TEMPERATURE="0.1"
OPENVIKING_VLM_MAX_RETRIES="3"
OPENVIKING_AUTO_GENERATE_L0="false"
OPENVIKING_AUTO_GENERATE_L1="false"

mkdir -p "${OPENVIKING_DIR}" "${OPENVIKING_WORKSPACE}"

inject_opengauss_config() {
    python3 -c "
import json, sys
conf_path = sys.argv[1]
with open(conf_path) as f:
    conf = json.load(f)
conf['storage']['vectordb']['opengauss'] = {
    'host': sys.argv[2],
    'port': int(sys.argv[3]),
    'user': sys.argv[4],
    'password': sys.argv[5],
    'db_name': sys.argv[6],
    'mode': sys.argv[7],
}
with open(conf_path, 'w') as f:
    json.dump(conf, f, indent=2)
" "${OV_CONF_PATH}" "${OG_HOST}" "${OG_PORT}" "${OG_USER}" "${OG_PASSWORD}" "${OG_DB_NAME}" "${OG_MODE}"
}

if [[ -f "${OV_CONF_PATH}" && "${OPENVIKING_REGENERATE_CONFIG}" != "1" ]]; then
    echo "[INFO] Using mounted/existing config at ${OV_CONF_PATH}"
else
    export OPENVIKING_SERVER_HOST OPENVIKING_SERVER_PORT OPENVIKING_ROOT_API_KEY OPENVIKING_WORKSPACE
    export OPENVIKING_VLM_PROVIDER OPENVIKING_VLM_API_KEY OPENVIKING_VLM_API_BASE
    export OPENVIKING_VLM_MODEL OPENVIKING_VLM_TEMPERATURE OPENVIKING_VLM_MAX_RETRIES
    export OPENVIKING_VECTORDB_BACKEND
    export OPENVIKING_AUTO_GENERATE_L0 OPENVIKING_AUTO_GENERATE_L1

    if [[ -n "${OPENVIKING_EMBEDDING_API_KEY}" ]]; then
        OPENVIKING_EMBEDDING_PROVIDER="${OPENVIKING_EMBEDDING_PROVIDER:-volcengine}"
        echo "[INFO] Using remote embedding: provider=${OPENVIKING_EMBEDDING_PROVIDER}, model=${OPENVIKING_EMBEDDING_MODEL}"

        export OPENVIKING_EMBEDDING_PROVIDER OPENVIKING_EMBEDDING_API_KEY OPENVIKING_EMBEDDING_API_BASE
        export OPENVIKING_EMBEDDING_MODEL OPENVIKING_EMBEDDING_DIMENSION OPENVIKING_EMBEDDING_INPUT

        envsubst < /app/ov.conf.template.json > "${OV_CONF_PATH}"
        echo "[INFO] Generated config with remote embedding at ${OV_CONF_PATH}"
    else
        echo "[INFO] No OPENVIKING_EMBEDDING_API_KEY set — using local embedding (bge-small-zh-v1.5-f16, dim=512)"

        envsubst < /app/ov.conf.local-embed.template.json > "${OV_CONF_PATH}"
        echo "[INFO] Generated config with local embedding at ${OV_CONF_PATH}"
    fi

    # VLM API Key 未设置时移除 vlm 配置块（避免 api_key 空字符串校验报错）
    if [[ -z "${OPENVIKING_VLM_API_KEY}" ]]; then
        python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    conf = json.load(f)
conf.pop('vlm', None)
with open(sys.argv[1], 'w') as f:
    json.dump(conf, f, indent=2)
" "${OV_CONF_PATH}"
        echo "[INFO] VLM not configured (no OPENVIKING_VLM_API_KEY)"
    else
        echo "[INFO] VLM configured: provider=${OPENVIKING_VLM_PROVIDER}, model=${OPENVIKING_VLM_MODEL}"
    fi

    if [[ "${OPENVIKING_VECTORDB_BACKEND}" == "opengauss" ]]; then
        echo "[INFO] VectorDB backend: opengauss (host=${OG_HOST}:${OG_PORT}, db=${OG_DB_NAME}, mode=${OG_MODE})"
        inject_opengauss_config
    else
        echo "[INFO] VectorDB backend: ${OPENVIKING_VECTORDB_BACKEND}"
    fi
fi

echo "[INFO] Starting OpenViking server on ${OPENVIKING_SERVER_HOST}:${OPENVIKING_SERVER_PORT}..."
exec python3 -m openviking.server.bootstrap --config "${OV_CONF_PATH}" --host "${OPENVIKING_SERVER_HOST}" --port "${OPENVIKING_SERVER_PORT}"
