#!/usr/bin/env bash
set -euo pipefail

# 与 OpenClaw 状态目录一致：优先 OPENCLAW_STATE_DIR，避免 OPENCLAW_HOME 造成的 .openclaw/.openclaw 嵌套
OPENCLAW_DIR="${OPENCLAW_STATE_DIR:-${OPENCLAW_HOME:-/root/.openclaw}}"
OPENVIKING_DIR="${OPENVIKING_HOME:-/root/.openviking}"
PLUGIN_DIR="${OPENCLAW_DIR}/extensions/memory-openviking"
OV_CONF_PATH="${OPENVIKING_CONFIG_FILE:-${OPENVIKING_DIR}/ov.conf}"
BOOTSTRAP_MARKER="${OPENCLAW_DIR}/.openviking_bootstrapped"

OPENVIKING_SERVER_HOST="${OPENVIKING_SERVER_HOST:-127.0.0.1}"
OPENVIKING_SERVER_PORT="${OPENVIKING_SERVER_PORT:-1933}"
OPENVIKING_AGFS_PORT="${OPENVIKING_AGFS_PORT:-1833}"
OPENVIKING_WORKSPACE="${OPENVIKING_WORKSPACE:-${OPENVIKING_DIR}/data}"
OPENVIKING_TARGET_URI="${OPENVIKING_TARGET_URI:-viking://user/memories}"
OPENVIKING_AUTO_RECALL="${OPENVIKING_AUTO_RECALL:-true}"
OPENVIKING_AUTO_CAPTURE="${OPENVIKING_AUTO_CAPTURE:-true}"
OPENVIKING_REGENERATE_CONFIG="${OPENVIKING_REGENERATE_CONFIG:-0}"
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
OPENCLAW_REAPPLY_CONFIG="${OPENCLAW_REAPPLY_CONFIG:-0}"
# 设为 0 或 false 时仅启用 OpenClaw，不生成 ov.conf、不启用 memory-openviking 插件
OPENVIKING_ENABLED="${OPENVIKING_ENABLED:-1}"

OPENVIKING_EMBEDDING_API_KEY="${OPENVIKING_EMBEDDING_API_KEY:-${OPENVIKING_ARK_API_KEY:-}}"
OPENVIKING_VLM_API_KEY="${OPENVIKING_VLM_API_KEY:-${OPENVIKING_ARK_API_KEY:-}}"
OPENVIKING_EMBEDDING_MODEL="${OPENVIKING_EMBEDDING_MODEL:-doubao-embedding-vision-250615}"
OPENVIKING_VLM_MODEL="${OPENVIKING_VLM_MODEL:-doubao-seed-1-8-251228}"
OPENVIKING_API_BASE="${OPENVIKING_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
# VLM / Embedding 可完全用环境变量区分：provider、api_base 各自独立，未设则用上面默认
OPENVIKING_VLM_PROVIDER="${OPENVIKING_VLM_PROVIDER:-volcengine}"
OPENVIKING_VLM_API_BASE="${OPENVIKING_VLM_API_BASE:-${OPENVIKING_API_BASE}}"
OPENVIKING_EMBEDDING_PROVIDER="${OPENVIKING_EMBEDDING_PROVIDER:-volcengine}"
OPENVIKING_EMBEDDING_API_BASE="${OPENVIKING_EMBEDDING_API_BASE:-${OPENVIKING_API_BASE}}"
OPENVIKING_EMBEDDING_DIMENSION="${OPENVIKING_EMBEDDING_DIMENSION:-1024}"
OPENVIKING_EMBEDDING_INPUT="${OPENVIKING_EMBEDDING_INPUT:-multimodal}"

# OpenClaw default model and provider API keys (optional; used only when bootstrap runs)
OPENCLAW_DEFAULT_MODEL="${OPENCLAW_DEFAULT_MODEL:-}"

mkdir -p "${OPENCLAW_DIR}" "${PLUGIN_DIR}" "${OPENVIKING_WORKSPACE}"

# 挂载宿主机目录到 OPENCLAW_DIR 时会把镜像内插件遮住，若挂载目录里没有插件则从镜像内副本补齐
if [[ ! -f "${PLUGIN_DIR}/openclaw.plugin.json" ]] && [[ -d /app/openclaw-plugin-default/memory-openviking ]]; then
  cp -a /app/openclaw-plugin-default/memory-openviking/. "${PLUGIN_DIR}/"
  echo "[INFO] Copied memory-openviking plugin into OpenClaw dir (mount had no plugin)."
fi

# --- OpenViking ov.conf: only when OPENVIKING_ENABLED and (missing or REGENERATE=1)
if [[ "${OPENVIKING_ENABLED}" == "1" || "${OPENVIKING_ENABLED}" == "true" ]]; then
  if [[ "${OPENVIKING_REGENERATE_CONFIG}" == "1" || ! -f "${OV_CONF_PATH}" ]]; then
    if [[ -z "${OPENVIKING_EMBEDDING_API_KEY}" || -z "${OPENVIKING_VLM_API_KEY}" ]]; then
      echo "[ERROR] OPENVIKING_EMBEDDING_API_KEY / OPENVIKING_VLM_API_KEY (or OPENVIKING_ARK_API_KEY) is required when generating ov.conf."
      exit 1
    fi
    export OPENVIKING_SERVER_HOST OPENVIKING_SERVER_PORT OPENVIKING_AGFS_PORT
    export OPENVIKING_WORKSPACE OPENVIKING_EMBEDDING_API_KEY OPENVIKING_VLM_API_KEY
    export OPENVIKING_EMBEDDING_MODEL OPENVIKING_VLM_MODEL OPENVIKING_API_BASE
    export OPENVIKING_VLM_PROVIDER OPENVIKING_VLM_API_BASE OPENVIKING_EMBEDDING_PROVIDER OPENVIKING_EMBEDDING_API_BASE
    export OPENVIKING_EMBEDDING_DIMENSION OPENVIKING_EMBEDDING_INPUT
    envsubst < /app/ov.conf.template.json > "${OV_CONF_PATH}"
    echo "[INFO] Generated OpenViking config at ${OV_CONF_PATH}"
  else
    echo "[INFO] Reusing existing OpenViking config at ${OV_CONF_PATH}"
  fi
else
  echo "[INFO] OpenViking disabled (OPENVIKING_ENABLED=${OPENVIKING_ENABLED}); only OpenClaw will run."
fi

# --- OpenClaw: bootstrap only when no existing user config and (no marker or REAPPLY=1)
# If openclaw.json exists (e.g. mounted) and REAPPLY!=1, never overwrite. On restart, marker exists so we skip.
RUN_OPENCLAW_BOOTSTRAP=0
if [[ "${OPENCLAW_REAPPLY_CONFIG}" == "1" ]]; then
  RUN_OPENCLAW_BOOTSTRAP=1
elif [[ -f "${OPENCLAW_DIR}/openclaw.json" ]]; then
  echo "[INFO] Found existing openclaw.json; skipping bootstrap (set OPENCLAW_REAPPLY_CONFIG=1 to override)."
elif [[ ! -f "${BOOTSTRAP_MARKER}" ]]; then
  RUN_OPENCLAW_BOOTSTRAP=1
fi

if [[ "${RUN_OPENCLAW_BOOTSTRAP}" == "1" ]]; then
  openclaw config set gateway.port "${OPENCLAW_GATEWAY_PORT}"
  openclaw config set gateway.mode local
  openclaw config set plugins.enabled true
  if [[ "${OPENVIKING_ENABLED}" == "1" || "${OPENVIKING_ENABLED}" == "true" ]]; then
    openclaw config set plugins.load.paths "[\"${PLUGIN_DIR}\"]" --json
    openclaw config set plugins.allow '["memory-openviking"]' --json
    openclaw config set plugins.slots.memory memory-openviking
    openclaw config set plugins.entries.memory-openviking.config.mode local
    openclaw config set plugins.entries.memory-openviking.config.configPath "${OV_CONF_PATH}"
    openclaw config set plugins.entries.memory-openviking.config.port "${OPENVIKING_SERVER_PORT}"
    openclaw config set plugins.entries.memory-openviking.config.targetUri "${OPENVIKING_TARGET_URI}"
    openclaw config set plugins.entries.memory-openviking.config.autoRecall "${OPENVIKING_AUTO_RECALL}" --json
    openclaw config set plugins.entries.memory-openviking.config.autoCapture "${OPENVIKING_AUTO_CAPTURE}" --json
  else
    openclaw config set plugins.load.paths '[]' --json
    openclaw config set plugins.allow '[]' --json
  fi

  # Default model (e.g. zai/glm-4.7 for 智谱, anthropic/claude-sonnet-4); see docs/openclaw-models-zh.md
  if [[ -n "${OPENCLAW_DEFAULT_MODEL}" ]]; then
    openclaw config set agents.defaults.model.primary "${OPENCLAW_DEFAULT_MODEL}" 2>/dev/null || true
  fi

  # 智谱：OpenClaw 官方 provider 为 zai，认证用 ZAI_API_KEY；兼容旧变量 ZHIPU_API_KEY
  if [[ -z "${ZAI_API_KEY:-}" && -n "${ZHIPU_API_KEY:-}" ]]; then
    export ZAI_API_KEY="${ZHIPU_API_KEY}"
  fi
  # Provider API keys from env (only set when non-empty)
  if [[ -n "${ZAI_API_KEY:-}" ]]; then
    openclaw config set models.providers.zai.apiKey "${ZAI_API_KEY}" 2>/dev/null || true
  fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    openclaw config set models.providers.anthropic.apiKey "${ANTHROPIC_API_KEY}" 2>/dev/null || true
  fi
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    openclaw config set models.providers.openai.apiKey "${OPENAI_API_KEY}" 2>/dev/null || true
  fi
  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    openclaw config set models.providers.openrouter.apiKey "${OPENROUTER_API_KEY}" 2>/dev/null || true
  fi
  if [[ -n "${MOONSHOT_API_KEY:-}" ]]; then
    openclaw config set models.providers.moonshot.apiKey "${MOONSHOT_API_KEY}" 2>/dev/null || true
  fi
  if [[ -n "${GOOGLE_API_KEY:-}" ]]; then
    openclaw config set models.providers.google.apiKey "${GOOGLE_API_KEY}" 2>/dev/null || true
  fi
  if [[ -n "${GROQ_API_KEY:-}" ]]; then
    openclaw config set models.providers.groq.apiKey "${GROQ_API_KEY}" 2>/dev/null || true
  fi
  # 火山引擎（与 OpenViking 同源 VLM，对话也用豆包时可复用同一 Key）
  if [[ -n "${VOLCANO_ENGINE_API_KEY:-}" ]]; then
    openclaw config set models.providers.volcengine.apiKey "${VOLCANO_ENGINE_API_KEY}" 2>/dev/null || true
  fi

  touch "${BOOTSTRAP_MARKER}"
  echo "[INFO] Applied OpenClaw/OpenViking bootstrap config."
fi

# Let plugin know which python to use.
cat > "${OPENCLAW_DIR}/openviking.env" <<EOF
export OPENVIKING_PYTHON='${OPENVIKING_PYTHON:-/usr/local/bin/python3}'
EOF
source "${OPENCLAW_DIR}/openviking.env"

if [[ "${OPENCLAW_SKIP_ONBOARD:-1}" != "1" ]]; then
  echo "[INFO] Running openclaw onboard..."
  openclaw onboard || true
fi

echo "[INFO] Starting OpenClaw gateway..."
exec openclaw gateway
