#!/bin/bash
#
# OpenClaw + OpenViking 一键安装脚本
# 使用方式: curl -fsSL https://raw.githubusercontent.com/OpenViking/OpenViking/main/examples/openclaw-memory-plugin/install.sh | bash
#
# 支持的环境变量:
#   REPO=owner/repo          - GitHub 仓库 (默认: OpenViking/OpenViking)
#   BRANCH=branch            - 克隆的分支 (默认: main)
#   OPENVIKING_INSTALL_YES=1 - 非交互模式 (等同于 -y)
#   SKIP_OPENCLAW=1          - 跳过 OpenClaw 安装 (已安装时使用)
#   SKIP_OPENVIKING=1        - 跳过 OpenViking 安装 (已安装时使用)
#

set -e

REPO="${REPO:-OpenViking/OpenViking}"
BRANCH="${BRANCH:-main}"
INSTALL_YES="${OPENVIKING_INSTALL_YES:-0}"
SKIP_OC="${SKIP_OPENCLAW:-0}"
SKIP_OV="${SKIP_OPENVIKING:-0}"

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
    echo "  # 安装 Python 3.10+（推荐 3.11）"
    echo "  # Ubuntu/Debian: sudo apt install python3.11 python3.11-venv"
    echo "  # 或从源码: https://www.python.org/downloads/"
    echo "  # 安装后运行: python3 --version 确认 >= 3.10"
    echo ""
  fi

  if printf '%s\n' "${missing[@]}" | grep -q "Node"; then
    echo "  # 安装 Node.js 22+"
    if [[ "$DISTRO" == "rhel" ]]; then
      echo "  curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -"
      echo "  sudo dnf install -y nodejs"
    elif [[ "$DISTRO" == "debian" ]]; then
      echo "  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -"
      echo "  sudo apt install -y nodejs"
    else
      echo "  # 使用 nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
      echo "  # 然后: nvm install 22 && nvm use 22"
    fi
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
    info "跳过 OpenClaw 安装 (SKIP_OPENCLAW=1)"
    return 0
  fi
  info "正在安装 OpenClaw..."
  npm install -g openclaw || {
    err "OpenClaw 安装失败，请检查 npm 权限或使用 sudo"
    exit 1
  }
  info "OpenClaw 安装完成 ✓"
}

install_openviking() {
  if [[ "$SKIP_OV" == "1" ]]; then
    info "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)"
    return 0
  fi
  info "正在安装 OpenViking (PyPI)..."
  python3 -m pip install --upgrade pip -q
  python3 -m pip install openviking || {
    err "OpenViking 安装失败，请检查 Python 版本 (需 >= 3.10) 及 pip"
    exit 1
  }
  info "OpenViking 安装完成 ✓"
}

run_setup_helper() {
  local gh_raw="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
  local cli_url="${gh_raw}/examples/openclaw-memory-plugin/setup-helper/cli.js"
  local tmp_dir
  tmp_dir=$(mktemp -d 2>/dev/null || echo "/tmp/openviking-install-$$")
  trap "rm -rf '$tmp_dir'" EXIT

  info "正在下载配置助手..."
  if ! curl -fsSL -o "$tmp_dir/cli.js" "$cli_url"; then
    err "下载配置助手失败: $cli_url"
    err "请检查网络或 REPO/BRANCH 配置"
    exit 1
  fi

  info "正在运行配置助手..."
  export OPENVIKING_GITHUB_RAW="$gh_raw"
  # 不设置 OPENVIKING_REPO，setup-helper 会通过 curl 从 GitHub 拉取插件
  if [[ "$INSTALL_YES" == "1" ]]; then
    node "$tmp_dir/cli.js" -y
  else
    node "$tmp_dir/cli.js"
  fi
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
  run_setup_helper

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
