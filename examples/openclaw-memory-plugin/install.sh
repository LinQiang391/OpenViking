#!/usr/bin/env bash
set -euo pipefail

REPO="${OV_MEMORY_REPO:-volcengine/OpenViking}"
DEFAULT_REF="${OV_MEMORY_DEFAULT_REF:-main}"
REF_OVERRIDE=""
SKIP_CHECKSUM="${SKIP_CHECKSUM:-0}"
SKIP_BUILD_TOOLS_CHECK="${SKIP_BUILD_TOOLS_CHECK:-0}"
AUTO_INSTALL_NODE="${AUTO_INSTALL_NODE:-1}"
AUTO_INSTALL_OPENCLAW="${AUTO_INSTALL_OPENCLAW:-1}"
AUTO_INSTALL_BUILD_TOOLS="${AUTO_INSTALL_BUILD_TOOLS:-0}"
USE_MIRROR="${USE_MIRROR:-1}"
OV_MEMORY_NODE_VERSION="${OV_MEMORY_NODE_VERSION:-22}"
NPM_REGISTRY_MIRROR="https://registry.npmmirror.com"
HELPER_ARGS=()

usage() {
  cat <<'EOF'
OpenViking Memory Installer for OpenClaw (Linux/macOS)

Usage:
  curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- -y

Options:
  --ref <ref>      Git ref for helper files (for example: ocm@0.1.0)
  -h, --help       Show this help

Environment:
  OV_MEMORY_VERSION      Override git ref used to download setup helper
  OV_MEMORY_REPO         Override GitHub repo (default: volcengine/OpenViking)
  OV_MEMORY_DEFAULT_REF  Fallback ref when OV_MEMORY_VERSION is not set (default: main)
  AUTO_INSTALL_NODE      Auto-install Node.js when missing/too old (default: 1)
  AUTO_INSTALL_OPENCLAW  Auto-install OpenClaw when missing (default: 1)
  AUTO_INSTALL_BUILD_TOOLS  Auto-install cmake/g++ when missing, no prompt (default: 0)
  USE_MIRROR             Use npmmirror for npm when installing OpenClaw (default: 1)
  OV_MEMORY_NODE_VERSION Node.js major/minor used by auto-install (default: 22)
  OPENVIKING_GITHUB_RAW  Override raw base URL used by helper and installer
  SKIP_CHECKSUM=1        Skip SHA256 checksum verification
  SKIP_BUILD_TOOLS_CHECK=1  Skip cmake/g++ check (use when tools are in PATH from conda etc.)

All other arguments are forwarded to the setup helper.
EOF
}

log() { printf '[openviking-installer] %s\n' "$*"; }
die() { printf '[openviking-installer] ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

node_major_version() {
  node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'
}

install_node_with_nvm() {
  local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  local nvm_install_url="https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh"
  export NVM_DIR="$nvm_dir"

  log "Installing Node.js ${OV_MEMORY_NODE_VERSION} via nvm..."
  if [[ ! -s "${nvm_dir}/nvm.sh" ]]; then
    curl -fsSL "$nvm_install_url" | bash || die "Failed to install nvm"
  fi

  # shellcheck source=/dev/null
  [ -s "${nvm_dir}/nvm.sh" ] && . "${nvm_dir}/nvm.sh" || die "Failed to load nvm"

  nvm install "${OV_MEMORY_NODE_VERSION}" || die "Failed to install Node.js ${OV_MEMORY_NODE_VERSION}"
  nvm use "${OV_MEMORY_NODE_VERSION}" >/dev/null || die "Failed to activate Node.js ${OV_MEMORY_NODE_VERSION}"
}

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    local major
    major="$(node_major_version)"
    if [[ -n "$major" && "$major" -ge 22 ]]; then
      log "Detected Node.js $(node -v)"
      return
    fi
    log "Detected Node.js $(node -v), but >=22 is required"
  else
    log "Node.js is not installed"
  fi

  [[ "$AUTO_INSTALL_NODE" == "1" ]] || die "Node.js >=22 is required. Set AUTO_INSTALL_NODE=1 or install Node manually."
  install_node_with_nvm

  command -v node >/dev/null 2>&1 || die "Node installation finished but node is still unavailable"
  local major
  major="$(node_major_version)"
  [[ -n "$major" && "$major" -ge 22 ]] || die "Node.js version is too old after installation: $(node -v)"
  log "Node.js ready: $(node -v)"
}

install_build_tools_sudo() {
  log "Installing build tools (cmake, g++) via package manager (sudo required)..."
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y gcc gcc-c++ cmake make || return 1
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y gcc gcc-c++ cmake make || return 1
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y build-essential cmake || return 1
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper -n install gcc-c++ cmake make || return 1
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add --no-cache g++ cmake make || return 1
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm base-devel cmake || return 1
  elif command -v brew >/dev/null 2>&1; then
    brew install cmake || return 1
  else
    return 1
  fi
}

install_build_tools_conda() {
  local conda_cmd=""
  command -v mamba >/dev/null 2>&1 && conda_cmd="mamba"
  command -v conda >/dev/null 2>&1 && [[ -z "$conda_cmd" ]] && conda_cmd="conda"
  [[ -z "$conda_cmd" ]] && return 1
  log "Installing build tools (cmake, g++) via $conda_cmd (no sudo)..."
  $conda_cmd install -y -c conda-forge cmake cxx-compiler 2>/dev/null && return 0
  $conda_cmd install -y cmake cxx-compiler 2>/dev/null && return 0
  $conda_cmd install -y cmake compilers 2>/dev/null && return 0
  $conda_cmd install -y cmake 2>/dev/null && return 0
  return 1
}

build_tools_manual_help() {
  printf '[openviking-installer] Build tools (cmake, g++) required by OpenClaw. Install options:
  With sudo:
    RHEL/CentOS/Fedora:  sudo dnf install -y gcc gcc-c++ cmake make
    Ubuntu/Debian:       sudo apt update && sudo apt install -y build-essential cmake
    openSUSE:            sudo zypper install -y gcc-c++ cmake make
    Alpine:              sudo apk add g++ cmake make
    Arch:                sudo pacman -Sy base-devel cmake
    macOS:               brew install cmake
  Without sudo (conda):  conda install -y cmake cxx-compiler
  Skip check:            SKIP_BUILD_TOOLS_CHECK=1 (if cmake/g++ already in PATH)
'
}

ensure_build_tools() {
  if [[ "$SKIP_BUILD_TOOLS_CHECK" == "1" ]]; then
    log "Skipping build tools check (SKIP_BUILD_TOOLS_CHECK=1)"
    return 0
  fi

  log "Checking build tools (cmake, g++)..."
  local missing=()
  command -v cmake >/dev/null 2>&1 || missing+=(cmake)
  command -v g++ >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || missing+=(g++)
  if [[ ${#missing[@]} -eq 0 ]]; then
    log "Build tools OK: cmake, g++"
    return
  fi

  local has_sudo=false has_conda=false
  command -v sudo >/dev/null 2>&1 && has_sudo=true
  command -v conda >/dev/null 2>&1 || command -v mamba >/dev/null 2>&1 && has_conda=true

  if ! "$has_sudo" && ! "$has_conda"; then
    build_tools_manual_help
    die "No sudo or conda available. Install cmake and g++ manually, or use conda. Set SKIP_BUILD_TOOLS_CHECK=1 if already installed."
  fi

  local install_it=0 method=""
  if [[ "$AUTO_INSTALL_BUILD_TOOLS" == "1" ]]; then
    if command -v sudo >/dev/null 2>&1; then
      method="sudo"
      install_it=1
    elif "$has_conda"; then
      method="conda"
      install_it=1
    fi
  elif [[ -t 1 ]] && [[ -c /dev/tty ]] 2>/dev/null; then
    local resp
    printf '[openviking-installer] Build tools missing: %s.\n' "${missing[*]}"
    if "$has_sudo"; then
      printf '  [s] Install via sudo (needs root password)\n'
    fi
    if "$has_conda"; then
      printf '  [c] Install via conda (no sudo, user-level)\n'
    fi
    printf '  [n] Skip, show manual instructions\n'
    printf '  Choice [s/c/n]: '
    read -r resp </dev/tty 2>/dev/null || true
    case "${resp:-s}" in
      [sS]) "$has_sudo" && method="sudo"  && install_it=1 ;;
      [cC]) "$has_conda" && method="conda" && install_it=1 ;;
      *)    install_it=0 ;;
    esac
  fi

  if [[ "$install_it" -eq 1 ]] && [[ -n "$method" ]]; then
    local ok=false
    if [[ "$method" == "sudo" ]] && "$has_sudo"; then
      install_build_tools_sudo && ok=true
    elif [[ "$method" == "conda" ]] && "$has_conda"; then
      install_build_tools_conda && ok=true
    fi
    if "$ok"; then
      command -v cmake >/dev/null 2>&1 || die "cmake still not found after install (restart shell to refresh PATH)"
      command -v g++ >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || die "g++/gcc still not found after install"
      log "Build tools OK: cmake, g++"
    else
      build_tools_manual_help
      die "Failed to install build tools. See instructions above."
    fi
  else
    build_tools_manual_help
    die "Run again and choose an option, or install manually, or set SKIP_BUILD_TOOLS_CHECK=1."
  fi
}

ensure_openclaw() {
  if command -v openclaw >/dev/null 2>&1; then
    log "Detected OpenClaw: $(openclaw --version 2>/dev/null || openclaw -v 2>/dev/null || echo 'installed')"
    return
  fi

  [[ "$AUTO_INSTALL_OPENCLAW" == "1" ]] || die "OpenClaw is not installed. Install it first: npm install -g openclaw"

  log "OpenClaw is not installed. Installing via npm..."
  local npm_opts=(-g openclaw)
  [[ "$USE_MIRROR" == "1" ]] && npm_opts+=(--registry="$NPM_REGISTRY_MIRROR")
  npm install "${npm_opts[@]}" || die "Failed to install OpenClaw. Run 'npm install -g openclaw' manually."

  command -v openclaw >/dev/null 2>&1 || die "OpenClaw installation finished but openclaw is still unavailable"
  log "OpenClaw ready: $(openclaw --version 2>/dev/null || openclaw -v 2>/dev/null || echo 'installed')"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      [[ $# -ge 2 ]] || die "--ref requires a value"
      REF_OVERRIDE="$2"
      shift 2
      ;;
    --ref=*)
      REF_OVERRIDE="${1#--ref=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      HELPER_ARGS+=("$1")
      shift
      ;;
  esac
done

require_cmd bash
require_cmd curl
ensure_node
ensure_build_tools
ensure_openclaw

REF="${OV_MEMORY_VERSION:-${REF_OVERRIDE:-$DEFAULT_REF}}"
if [[ -z "$REF" ]]; then
  die "Resolved ref is empty; set OV_MEMORY_VERSION or pass --ref"
fi
if [[ -z "${OV_MEMORY_VERSION:-}" && -z "$REF_OVERRIDE" ]]; then
  log "OV_MEMORY_VERSION is not set; using default ref: ${REF}"
fi

if [[ -n "${OPENVIKING_GITHUB_RAW:-}" ]]; then
  RAW_BASE="$OPENVIKING_GITHUB_RAW"
else
  RAW_BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
  export OPENVIKING_GITHUB_RAW="$RAW_BASE"
fi

HELPER_REL="examples/openclaw-memory-plugin/setup-helper/cli.js"
CHECKSUM_REL="${HELPER_REL}.sha256"
HELPER_URL="${RAW_BASE}/${HELPER_REL}"
CHECKSUM_URL="${RAW_BASE}/${CHECKSUM_REL}"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

HELPER_PATH="${TMP_DIR}/cli.js"
CHECKSUM_PATH="${TMP_DIR}/cli.js.sha256"
PKG_PATH="${TMP_DIR}/package.json"

log "Using ref: ${REF}"
log "Downloading setup helper..."
curl -fsSL "$HELPER_URL" -o "$HELPER_PATH" || die "Failed to download helper: $HELPER_URL"

if [[ "$SKIP_CHECKSUM" != "1" ]]; then
  log "Downloading checksum..."
  curl -fsSL "$CHECKSUM_URL" -o "$CHECKSUM_PATH" || die "Failed to download checksum: $CHECKSUM_URL"
  EXPECTED="$(awk 'NF { print $1; exit }' "$CHECKSUM_PATH" | tr '[:upper:]' '[:lower:]')"
  [[ -n "$EXPECTED" ]] || die "Checksum file is empty: $CHECKSUM_URL"

  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL="$(sha256sum "$HELPER_PATH" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
  elif command -v shasum >/dev/null 2>&1; then
    ACTUAL="$(shasum -a 256 "$HELPER_PATH" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
  else
    die "No SHA256 tool found (sha256sum/shasum). Set SKIP_CHECKSUM=1 to bypass."
  fi

  [[ "$ACTUAL" == "$EXPECTED" ]] || die "Checksum mismatch for setup helper"
  log "Checksum verification passed"
else
  log "Skipping checksum verification (SKIP_CHECKSUM=1)"
fi

cat >"$PKG_PATH" <<'EOF'
{"type":"module"}
EOF

log "Running setup helper..."
node "$HELPER_PATH" "${HELPER_ARGS[@]}"
