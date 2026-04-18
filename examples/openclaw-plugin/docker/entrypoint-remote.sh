#!/usr/bin/env bash
set -euo pipefail

# Remote-only entrypoint: OpenClaw + memory-openviking plugin connecting to external OpenViking service

OPENCLAW_DIR="${OPENCLAW_STATE_DIR:-${OPENCLAW_HOME:-/root/.openclaw}}"
BOOTSTRAP_MARKER="${OPENCLAW_DIR}/.openviking_bootstrapped"

# 自动检测插件目录（兼容 openviking / memory-openviking 两种命名）
if [[ -d "${OPENCLAW_DIR}/extensions/openviking" ]]; then
  PLUGIN_DIR="${OPENCLAW_DIR}/extensions/openviking"
  PLUGIN_ID="openviking"
elif [[ -d "${OPENCLAW_DIR}/extensions/memory-openviking" ]]; then
  PLUGIN_DIR="${OPENCLAW_DIR}/extensions/memory-openviking"
  PLUGIN_ID="memory-openviking"
else
  PLUGIN_DIR="${OPENCLAW_DIR}/extensions/openviking"
  PLUGIN_ID="openviking"
fi

# OpenClaw gateway settings - bind to lan (0.0.0.0) for container access
OPENCLAW_GATEWAY_BIND="${OPENCLAW_GATEWAY_BIND:-lan}"
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
OPENCLAW_REAPPLY_CONFIG="${OPENCLAW_REAPPLY_CONFIG:-0}"

# OpenViking remote connection (required)
OPENVIKING_BASE_URL="${OPENVIKING_BASE_URL:-}"
OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-}"
OPENVIKING_AGENT_ID="${OPENVIKING_AGENT_ID:-main}"
OPENVIKING_TARGET_URI="${OPENVIKING_TARGET_URI:-viking://user/memories}"
OPENVIKING_AUTO_RECALL="${OPENVIKING_AUTO_RECALL:-true}"
OPENVIKING_AUTO_CAPTURE="${OPENVIKING_AUTO_CAPTURE:-true}"

# OpenClaw default model and provider API keys (optional)
OPENCLAW_DEFAULT_MODEL="${OPENCLAW_DEFAULT_MODEL:-}"
OPENCLAW_LLM_API_KEY="${OPENCLAW_LLM_API_KEY:-}"
OPENCLAW_LLM_API_BASE="${OPENCLAW_LLM_API_BASE:-}"

mkdir -p "${OPENCLAW_DIR}" "${PLUGIN_DIR}"

# Copy plugin from default backup if mounted directory doesn't have it
if [[ ! -f "${PLUGIN_DIR}/openclaw.plugin.json" ]]; then
  for candidate in openviking memory-openviking; do
    if [[ -d "/app/openclaw-plugin-default/${candidate}" ]]; then
      cp -a "/app/openclaw-plugin-default/${candidate}/." "${PLUGIN_DIR}/"
      echo "[INFO] Copied ${candidate} plugin into ${PLUGIN_DIR}."
      break
    fi
  done
fi

# Validate required remote configuration
if [[ -z "${OPENVIKING_BASE_URL}" ]]; then
  echo "[ERROR] OPENVIKING_BASE_URL is required for remote mode."
  echo "  Example: OPENVIKING_BASE_URL=http://your-openviking-server:1933"
  exit 1
fi

# --- OpenClaw bootstrap
RUN_OPENCLAW_BOOTSTRAP=0
if [[ "${OPENCLAW_REAPPLY_CONFIG}" == "1" ]]; then
  RUN_OPENCLAW_BOOTSTRAP=1
elif [[ -f "${OPENCLAW_DIR}/openclaw.json" ]]; then
  echo "[INFO] Found existing openclaw.json; skipping bootstrap (set OPENCLAW_REAPPLY_CONFIG=1 to override)."
elif [[ ! -f "${BOOTSTRAP_MARKER}" ]]; then
  RUN_OPENCLAW_BOOTSTRAP=1
fi

if [[ "${RUN_OPENCLAW_BOOTSTRAP}" == "1" ]]; then
  openclaw config set gateway.bind "${OPENCLAW_GATEWAY_BIND}"
  openclaw config set gateway.port "${OPENCLAW_GATEWAY_PORT}"
  openclaw config set gateway.mode local

  openclaw config set plugins.enabled true
  openclaw config set plugins.load.paths "[\"${PLUGIN_DIR}\"]" --json
  openclaw config set plugins.allow "[\"${PLUGIN_ID}\"]" --json
  openclaw config set plugins.slots.contextEngine "${PLUGIN_ID}"

  openclaw config set "plugins.entries.${PLUGIN_ID}.config.mode" remote
  openclaw config set "plugins.entries.${PLUGIN_ID}.config.baseUrl" "${OPENVIKING_BASE_URL}"
  if [[ -n "${OPENVIKING_API_KEY}" ]]; then
    openclaw config set "plugins.entries.${PLUGIN_ID}.config.apiKey" "${OPENVIKING_API_KEY}"
  fi
  openclaw config set "plugins.entries.${PLUGIN_ID}.config.agentId" "${OPENVIKING_AGENT_ID}"
  openclaw config set "plugins.entries.${PLUGIN_ID}.config.targetUri" "${OPENVIKING_TARGET_URI}"
  openclaw config set "plugins.entries.${PLUGIN_ID}.config.autoRecall" "${OPENVIKING_AUTO_RECALL}" --json
  openclaw config set "plugins.entries.${PLUGIN_ID}.config.autoCapture" "${OPENVIKING_AUTO_CAPTURE}" --json

  if [[ -n "${OPENCLAW_DEFAULT_MODEL}" ]]; then
    openclaw config set agents.defaults.model.primary "${OPENCLAW_DEFAULT_MODEL}" 2>/dev/null || true
  fi

  # Provider API keys from env
  if [[ -z "${ZAI_API_KEY:-}" && -n "${ZHIPU_API_KEY:-}" ]]; then
    export ZAI_API_KEY="${ZHIPU_API_KEY}"
  fi
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
  if [[ -n "${VOLCANO_ENGINE_API_KEY:-}" ]]; then
    openclaw config set models.providers.volcengine.apiKey "${VOLCANO_ENGINE_API_KEY}" 2>/dev/null || true
  fi

  # 通用 LLM 配置（OPENCLAW_LLM_API_KEY / OPENCLAW_LLM_API_BASE）
  # 直接写 openclaw.json 的 models 部分（openclaw config set 不支持 models.providers 路径）
  if [[ -n "${OPENCLAW_DEFAULT_MODEL}" && -n "${OPENCLAW_LLM_API_KEY}" ]]; then
    _oc_provider=""
    _oc_model_id=""
    if [[ "${OPENCLAW_DEFAULT_MODEL}" == */* ]]; then
      _oc_provider="${OPENCLAW_DEFAULT_MODEL%%/*}"
      _oc_model_id="${OPENCLAW_DEFAULT_MODEL#*/}"
    else
      _oc_provider="volcengine"
      _oc_model_id="${OPENCLAW_DEFAULT_MODEL}"
    fi

    # 用 node 直接写入 openclaw.json 的 models.providers（与 pytest configure_models 一致）
    node -e "
const fs = require('fs');
const cfgPath = '${OPENCLAW_DIR}/openclaw.json';
let cfg = {};
try { cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8')); } catch(e) {}
if (!cfg.models) cfg.models = {};
cfg.models.mode = 'merge';
if (!cfg.models.providers) cfg.models.providers = {};
const p = cfg.models.providers['${_oc_provider}'] || {};
p.api = 'openai-completions';
if ('${OPENCLAW_LLM_API_BASE}') p.baseUrl = '${OPENCLAW_LLM_API_BASE}';
if (!p.models || !p.models.length) p.models = [{id:'${_oc_model_id}',name:'${_oc_model_id}'}];
cfg.models.providers['${_oc_provider}'] = p;
fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2));
" 2>/dev/null || true

    # 写入 auth-profiles.json
    AUTH_DIR="${OPENCLAW_DIR}/agents/main/agent"
    mkdir -p "${AUTH_DIR}"
    cat > "${AUTH_DIR}/auth-profiles.json" <<EOAUTH
{
  "version": 1,
  "profiles": {
    "${_oc_provider}:default": {
      "type": "api_key",
      "provider": "${_oc_provider}",
      "key": "${OPENCLAW_LLM_API_KEY}"
    }
  }
}
EOAUTH
    echo "[INFO] Configured LLM: model=${OPENCLAW_DEFAULT_MODEL} provider=${_oc_provider}"
  fi

  touch "${BOOTSTRAP_MARKER}"
  echo "[INFO] Applied OpenClaw bootstrap config (remote mode)."
fi

if [[ "${OPENCLAW_SKIP_ONBOARD:-1}" != "1" ]]; then
  echo "[INFO] Running openclaw onboard..."
  openclaw onboard || true
fi

echo "[INFO] Starting OpenClaw gateway (remote mode, connecting to ${OPENVIKING_BASE_URL})..."
exec openclaw gateway
