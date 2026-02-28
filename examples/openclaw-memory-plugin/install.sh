#!/usr/bin/env bash
set -euo pipefail

REPO="${OV_MEMORY_REPO:-volcengine/OpenViking}"
DEFAULT_REF="${OV_MEMORY_DEFAULT_REF:-main}"
REF_OVERRIDE=""
SKIP_CHECKSUM="${SKIP_CHECKSUM:-0}"
AUTO_INSTALL_NODE="${AUTO_INSTALL_NODE:-1}"
OV_MEMORY_NODE_VERSION="${OV_MEMORY_NODE_VERSION:-22}"
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
  OV_MEMORY_NODE_VERSION Node.js major/minor used by auto-install (default: 22)
  OPENVIKING_GITHUB_RAW  Override raw base URL used by helper and installer
  SKIP_CHECKSUM=1        Skip SHA256 checksum verification

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
if ! command -v openclaw >/dev/null 2>&1; then
  die "OpenClaw is not installed. Install it first: npm install -g openclaw"
fi

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
