#!/bin/bash
#
# OpenClaw + OpenViking 一键安装脚本
# 使用方式: curl -fsSL https://raw.githubusercontent.com/OpenViking/OpenViking/main/examples/openclaw-memory-plugin/install.sh | bash
#
# 支持的环境变量:
#   REPO=owner/repo          - GitHub 仓库 (默认: OpenViking/OpenViking)
#   BRANCH=branch            - 克隆的分支 (默认: main)
#   OPENVIKING_INSTALL_YES=1 - 非交互模式 (等同于 -y)
#   SKIP_OPENCLAW=1          - 跳过 OpenClaw 校验
#   SKIP_OPENVIKING=1        - 跳过 OpenViking 安装 (已安装时使用)
#   NPM_REGISTRY=url         - npm 镜像源 (默认: https://registry.npmmirror.com)
#   PIP_INDEX_URL=url        - pip 镜像源 (默认: https://pypi.tuna.tsinghua.edu.cn/simple)
#

set -e

REPO="${REPO:-OpenViking/OpenViking}"
BRANCH="${BRANCH:-main}"
INSTALL_YES="${OPENVIKING_INSTALL_YES:-0}"
SKIP_OC="${SKIP_OPENCLAW:-0}"
SKIP_OV="${SKIP_OPENVIKING:-0}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
HOME_DIR="${HOME:-$USERPROFILE}"
OPENCLAW_DIR="${HOME_DIR}/.openclaw"
OPENVIKING_DIR="${HOME_DIR}/.openviking"
PLUGIN_DEST="${OPENCLAW_DIR}/extensions/memory-openviking"
DEFAULT_SERVER_PORT=1933
DEFAULT_AGFS_PORT=1833
DEFAULT_VLM_MODEL="doubao-seed-1-8-251228"
DEFAULT_EMBED_MODEL="doubao-embedding-vision-250615"
SELECTED_SERVER_PORT="${DEFAULT_SERVER_PORT}"

# 解析 -y 参数 (通过 curl | bash -s -y 传入)
for arg in "$@"; do
  [[ "$arg" == "-y" || "$arg" == "--yes" ]] && INSTALL_YES="1"
  [[ "$arg" == "-h" || "$arg" == "--help" ]] && {
    echo "Usage: curl -fsSL <INSTALL_URL> | bash [-s -y]"
    echo ""
    echo "Options:"
    echo "  -y, --yes   Non-interactive mode"
    echo "  -h, --help  Show this help"
    echo ""
    echo "Env vars: REPO, BRANCH, OPENVIKING_INSTALL_YES, SKIP_OPENCLAW, SKIP_OPENVIKING"
    exit 0
  }
done

# 颜色与输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }
bold()  { echo -e "${BOLD}$1${NC}"; }

# 检测系统
detect_os() {
  case "$(uname -s)" in
    Linux*)   OS="linux";;
    Darwin*)  OS="macos";;
    CYGWIN*|MINGW*|MSYS*) OS="windows";;
    *)        OS="unknown";;
  esac
  if [[ "$OS" == "windows" ]]; then
    err "Windows 暂不支持此一键安装脚本，请参考 INSTALL.md 或 INSTALL-ZH.md 手动安装。"
    exit 1
  fi
}

# 检测 Linux 发行版
detect_distro() {
  DISTRO="unknown"
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release 2>/dev/null || true
    case "${ID:-}" in
      ubuntu|debian|linuxmint) DISTRO="debian";;
      fedora|rhel|centos|rocky|almalinux|openeuler) DISTRO="rhel";;
    esac
  fi
  if command -v apt &>/dev/null; then
    DISTRO="debian"
  elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
    DISTRO="rhel"
  fi
}

# ─── 环境校验 ───

check_python() {
  local py="${OPENVIKING_PYTHON:-python3}"
  local out
  if ! out=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null); then
    echo "fail|$py|Python 未找到，请安装 Python >= 3.10"
    return 1
  fi
  local major minor
  IFS=. read -r major minor <<< "$out"
  if [[ "$major" -lt 3 ]] || [[ "$major" -eq 3 && "$minor" -lt 10 ]]; then
    echo "fail|$out|Python 版本 $out 过低，需要 >= 3.10"
    return 1
  fi
  echo "ok|$out|$py"
  return 0
}

check_node() {
  local out
  if ! out=$(node -v 2>/dev/null); then
    echo "fail||Node.js 未找到，请安装 Node.js >= 22"
    return 1
  fi
  local v="${out#v}"
  local major
  major="${v%%.*}"
  if [[ -z "$major" ]] || [[ "$major" -lt 22 ]]; then
    echo "fail|$out|Node.js 版本 $out 过低，需要 >= 22"
    return 1
  fi
  echo "ok|$out|node"
  return 0
}

# 输出缺失组件的安装指引
print_install_hints() {
  local missing=("$@")
  bold "\n═══════════════════════════════════════════════════════════"
  bold "  环境校验未通过，请先安装以下缺失组件："
  bold "═══════════════════════════════════════════════════════════\n"

  for item in "${missing[@]}"; do
    local name="${item%%|*}"
    local rest="${item#*|}"
    err "缺失: $name"
    [[ -n "$rest" ]] && echo "  $rest"
    echo ""
  done

  detect_distro
  echo "根据你的系统 ($DISTRO)，可执行以下命令安装："
  echo ""

  if printf '%s\n' "${missing[@]}" | grep -q "Python"; then
    echo "  # 普通用户安装 Python 3.10+（推荐 pyenv）"
    echo "  curl https://pyenv.run | bash"
    echo "  export PATH=\"\$HOME/.pyenv/bin:\$PATH\""
    echo "  eval \"\$(pyenv init -)\""
    echo "  pyenv install 3.11.12"
    echo "  pyenv global 3.11.12"
    echo "  python3 --version    # 确认 >= 3.10"
    echo ""
  fi

  if printf '%s\n' "${missing[@]}" | grep -q "Node"; then
    echo "  # 普通用户安装 Node.js 22+（nvm）"
    echo "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
    echo "  source ~/.bashrc"
    echo "  nvm install 22"
    echo "  nvm use 22"
    echo "  node -v            # 确认 >= v22"
    echo ""
  fi

  bold "安装完成后，请重新运行本脚本。"
  bold "详细说明见: https://github.com/${REPO}/blob/${BRANCH}/examples/openclaw-memory-plugin/INSTALL-ZH.md"
  echo ""
  exit 1
}

# 执行环境校验
validate_environment() {
  info "正在校验 OpenViking 运行环境..."
  echo ""

  local missing=()
  local r

  r=$(check_python) || missing+=("Python 3.10+ | $(echo "$r" | cut -d'|' -f3)")
  if [[ "${r%%|*}" == "ok" ]]; then
    info "  Python: $(echo "$r" | cut -d'|' -f2) ✓"
  fi

  r=$(check_node) || missing+=("Node.js 22+ | $(echo "$r" | cut -d'|' -f3)")
  if [[ "${r%%|*}" == "ok" ]]; then
    info "  Node.js: $(echo "$r" | cut -d'|' -f2) ✓"
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo ""
    print_install_hints "${missing[@]}"
  fi

  echo ""
  info "环境校验通过 ✓"
  echo ""
}

# ─── 安装流程 ───

install_openclaw() {
  if [[ "$SKIP_OC" == "1" ]]; then
    info "跳过 OpenClaw 校验 (SKIP_OPENCLAW=1)"
    return 0
  fi
  info "正在校验 OpenClaw..."
  if command -v openclaw >/dev/null 2>&1; then
    info "OpenClaw 已安装 ✓"
    return 0
  fi

  err "未检测到 OpenClaw，请先手动安装后再执行本脚本"
  echo ""
  echo "推荐命令（普通用户，国内镜像）："
  echo "  npm install -g openclaw --registry ${NPM_REGISTRY}"
  echo ""
  echo "如遇全局权限问题，建议先用 nvm 安装 Node 后再执行上述命令。"
  echo "安装完成后，运行："
  echo "  openclaw --version"
  echo "  openclaw onboard"
  echo ""
  exit 1
}

install_openviking() {
  if [[ "$SKIP_OV" == "1" ]]; then
    info "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)"
    return 0
  fi
  info "正在安装 OpenViking (PyPI)..."
  info "使用 pip 镜像源: ${PIP_INDEX_URL}"
  python3 -m pip install --upgrade pip -q -i "${PIP_INDEX_URL}"
  python3 -m pip install openviking -i "${PIP_INDEX_URL}" || {
    err "OpenViking 安装失败，请检查 Python 版本 (需 >= 3.10) 及 pip"
    exit 1
  }
  info "OpenViking 安装完成 ✓"
}

configure_openviking_conf() {
  mkdir -p "${OPENVIKING_DIR}"

  local workspace="${OPENVIKING_DIR}/data"
  local server_port="${DEFAULT_SERVER_PORT}"
  local agfs_port="${DEFAULT_AGFS_PORT}"
  local vlm_model="${DEFAULT_VLM_MODEL}"
  local embedding_model="${DEFAULT_EMBED_MODEL}"
  local api_key="${OPENVIKING_ARK_API_KEY:-}"
  local conf_path="${OPENVIKING_DIR}/ov.conf"
  local api_json="null"

  if [[ "$INSTALL_YES" != "1" ]]; then
    echo ""
    read -r -p "OpenViking 数据目录 [${workspace}]: " _workspace
    read -r -p "OpenViking HTTP 端口 [${server_port}]: " _server_port
    read -r -p "AGFS 端口 [${agfs_port}]: " _agfs_port
    read -r -p "VLM 模型 [${vlm_model}]: " _vlm_model
    read -r -p "Embedding 模型 [${embedding_model}]: " _embedding_model
    read -r -p "火山引擎 Ark API Key（可留空）: " _api_key

    workspace="${_workspace:-$workspace}"
    server_port="${_server_port:-$server_port}"
    agfs_port="${_agfs_port:-$agfs_port}"
    vlm_model="${_vlm_model:-$vlm_model}"
    embedding_model="${_embedding_model:-$embedding_model}"
    api_key="${_api_key:-$api_key}"
  fi

  if [[ -n "${api_key}" ]]; then
    api_json="\"${api_key}\""
  fi

  mkdir -p "${workspace}"
  cat > "${conf_path}" <<EOF
{
  "server": {
    "host": "127.0.0.1",
    "port": ${server_port},
    "root_api_key": null,
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "${workspace}",
    "vectordb": { "name": "context", "backend": "local", "project": "default" },
    "agfs": { "port": ${agfs_port}, "log_level": "warn", "backend": "local", "timeout": 10, "retry_times": 3 }
  },
  "embedding": {
    "dense": {
      "backend": "volcengine",
      "api_key": ${api_json},
      "model": "${embedding_model}",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "backend": "volcengine",
    "api_key": ${api_json},
    "model": "${vlm_model}",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
EOF
  SELECTED_SERVER_PORT="${server_port}"
  info "已生成配置: ${conf_path}"
}

download_plugin() {
  local gh_raw="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
  local files=(
    "examples/openclaw-memory-plugin/index.ts"
    "examples/openclaw-memory-plugin/config.ts"
    "examples/openclaw-memory-plugin/openclaw.plugin.json"
    "examples/openclaw-memory-plugin/package.json"
    "examples/openclaw-memory-plugin/package-lock.json"
    "examples/openclaw-memory-plugin/.gitignore"
  )

  mkdir -p "${PLUGIN_DEST}"
  info "正在下载 memory-openviking 插件..."
  for rel in "${files[@]}"; do
    local name="${rel##*/}"
    local url="${gh_raw}/${rel}"
    curl -fsSL -o "${PLUGIN_DEST}/${name}" "${url}" || {
      err "下载失败: ${url}"
      exit 1
    }
  done
  (cd "${PLUGIN_DEST}" && npm install --no-audit --no-fund) || {
    err "插件依赖安装失败: ${PLUGIN_DEST}"
    exit 1
  }
  info "插件部署完成: ${PLUGIN_DEST}"
}

configure_openclaw_plugin() {
  local server_port="${SELECTED_SERVER_PORT}"
  local config_path="~/.openviking/ov.conf"
  info "正在配置 OpenClaw 插件..."

  openclaw config set plugins.enabled true
  openclaw config set plugins.allow '["memory-openviking"]' --json
  openclaw config set gateway.mode local
  openclaw config set plugins.slots.memory memory-openviking
  openclaw config set plugins.load.paths "[\"${PLUGIN_DEST}\"]" --json
  openclaw config set plugins.entries.memory-openviking.config.mode local
  openclaw config set plugins.entries.memory-openviking.config.configPath "${config_path}"
  openclaw config set plugins.entries.memory-openviking.config.port "${server_port}"
  openclaw config set plugins.entries.memory-openviking.config.targetUri viking://
  openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
  openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json
  info "OpenClaw 插件配置完成"
}

write_openviking_env() {
  local py_path
  py_path="$(command -v python3 || command -v python || true)"
  mkdir -p "${OPENCLAW_DIR}"
  cat > "${OPENCLAW_DIR}/openviking.env" <<EOF
export OPENVIKING_PYTHON='${py_path}'
EOF
  info "已生成环境文件: ${OPENCLAW_DIR}/openviking.env"
}

# ─── 主流程 ───

main() {
  echo ""
  bold "🦣 OpenClaw + OpenViking 一键安装"
  echo ""

  detect_os
  validate_environment

  install_openclaw
  install_openviking
  configure_openviking_conf
  download_plugin
  configure_openclaw_plugin
  write_openviking_env

  echo ""
  bold "═══════════════════════════════════════════════════════════"
  bold "  安装完成！"
  bold "═══════════════════════════════════════════════════════════"
  echo ""
  info "启动方式 (Linux/macOS):"
  echo "  source ~/.openclaw/openviking.env && openclaw gateway"
  echo ""
  info "首次使用请配置火山引擎 Ark API Key（编辑 ~/.openviking/ov.conf）"
  echo "  获取地址: https://console.volcengine.com/ark"
  echo ""
}

main "$@"
