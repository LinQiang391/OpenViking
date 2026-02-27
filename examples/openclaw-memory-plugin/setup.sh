#!/bin/bash
set -e

# OpenViking + OpenClaw 记忆插件 一键安装脚本
# Usage: curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-memory-plugin/setup.sh | bash
# Non-interactive: curl -fsSL ... | bash -s -- -y
# Custom repo: REPO=volcengine/OpenViking curl -fsSL ... | bash
# Custom branch: BRANCH=main curl -fsSL ... | bash

REPO="${REPO:-volcengine/OpenViking}"
BRANCH="${BRANCH:-main}"
GITHUB_RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
SETUP_HELPER_URL="${GITHUB_RAW}/examples/openclaw-memory-plugin/setup-helper/cli.js"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check required commands
check_requirements() {
    if ! command -v curl &>/dev/null; then
        error "curl is required. Please install curl and try again."
    fi

    if ! command -v node &>/dev/null; then
        error "Node.js is required (>= 22). Please install Node.js first. See INSTALL-ZH.md for instructions."
    fi

    local node_version
    node_version=$(node -v 2>/dev/null | sed 's/^v//' | cut -d. -f1)
    if [[ -z "$node_version" ]] || [[ "$node_version" -lt 22 ]]; then
        error "Node.js >= 22 is required. Current: $(node -v 2>/dev/null || echo 'not found'). See INSTALL-ZH.md."
    fi

    info "Node.js $(node -v) detected"
}

# Install OpenClaw if not present
ensure_openclaw() {
    if command -v openclaw &>/dev/null; then
        info "OpenClaw is already installed: $(openclaw --version 2>/dev/null || echo 'ok')"
        return 0
    fi

    info "Installing OpenClaw..."
    if npm install -g openclaw --registry=https://registry.npmmirror.com 2>/dev/null; then
        info "OpenClaw installed successfully"
    elif npm install -g openclaw 2>/dev/null; then
        info "OpenClaw installed successfully"
    else
        error "Failed to install OpenClaw. Try: npm install -g openclaw --registry=https://registry.npmmirror.com"
    fi

    info "Run 'openclaw onboard' to configure your LLM (if not done yet)"
}

# Download and run setup-helper
run_setup_helper() {
    info "Downloading setup-helper..."
    TEMP_DIR=$(mktemp -d)
    trap "rm -rf $TEMP_DIR" EXIT

    local cli_js="${TEMP_DIR}/cli.js"
    if ! curl -fsSL -o "$cli_js" "$SETUP_HELPER_URL"; then
        error "Failed to download setup-helper from $SETUP_HELPER_URL"
    fi

    # When run from cloned repo (e.g. sh examples/.../setup.sh), pass OPENVIKING_REPO
    # so PyPI wheel Illegal instruction fallback can suggest source install from local repo
    if [[ -z "$OPENVIKING_REPO" ]]; then
        local script_path="${BASH_SOURCE[0]:-$0}"
        local script_dir
        script_dir="$(cd "$(dirname "$script_path")" 2>/dev/null && pwd)"
        if [[ -n "$script_dir" ]] && [[ -f "$script_dir/../../pyproject.toml" ]]; then
            export OPENVIKING_REPO="$(cd "$script_dir/../.." && pwd)"
            info "Detected OpenViking repo: $OPENVIKING_REPO (source install available if PyPI fails)"
        fi
    fi

    info "Running setup-helper..."
    export OPENVIKING_GITHUB_RAW="$GITHUB_RAW"
    node "$cli_js" "$@"
}

main() {
    echo ""
    info "OpenViking + OpenClaw 记忆插件 一键安装"
    echo ""

    check_requirements
    ensure_openclaw
    run_setup_helper "$@"
}

main "$@"
