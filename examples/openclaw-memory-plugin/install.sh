#!/usr/bin/env bash
set -euo pipefail

REPO="${OV_MEMORY_REPO:-LinQiang391/OpenViking}"
DEFAULT_REF="${OV_MEMORY_DEFAULT_REF:-setup}"
REF_OVERRIDE=""
SKIP_CHECKSUM="${SKIP_CHECKSUM:-0}"
SKIP_BUILD_TOOLS_CHECK="${SKIP_BUILD_TOOLS_CHECK:-0}"
AUTO_INSTALL_NODE="${AUTO_INSTALL_NODE:-1}"
AUTO_INSTALL_OPENCLAW="${AUTO_INSTALL_OPENCLAW:-1}"
AUTO_INSTALL_BUILD_TOOLS="${AUTO_INSTALL_BUILD_TOOLS:-0}"
AUTO_INSTALL_PYTHON="${AUTO_INSTALL_PYTHON:-1}"
AUTO_INSTALL_XPM="${AUTO_INSTALL_XPM:-1}"
ALLOW_SUDO_INSTALL="${ALLOW_SUDO_INSTALL:-0}"
AUTO_INSTALL_MICROMAMBA="${AUTO_INSTALL_MICROMAMBA:-1}"
OV_MEMORY_MM_ENV="${OV_MEMORY_MM_ENV:-$HOME/.openviking-installer-env}"
OV_MICROMAMBA_URL="${OV_MICROMAMBA_URL:-}"
OV_MICROMAMBA_CHANNEL="${OV_MICROMAMBA_CHANNEL:-conda-forge}"
OV_MICROMAMBA_CREATE_TIMEOUT="${OV_MICROMAMBA_CREATE_TIMEOUT:-1800}"
OV_MICROMAMBA_PROGRESS_ESTIMATE="${OV_MICROMAMBA_PROGRESS_ESTIMATE:-600}"
OV_PREFER_CN_MIRROR="${OV_PREFER_CN_MIRROR:-1}"
OV_NVM_INSTALL_URL="${OV_NVM_INSTALL_URL:-}"
OV_NODE_VERSION="${OV_NODE_VERSION:-22.22.0}"
OV_ALLOW_NVM_FALLBACK="${OV_ALLOW_NVM_FALLBACK:-0}"
USE_MIRROR="${USE_MIRROR:-1}"
OV_MEMORY_NODE_VERSION="${OV_MEMORY_NODE_VERSION:-22}"
OV_DOWNLOAD_RETRY="${OV_DOWNLOAD_RETRY:-3}"
OV_DOWNLOAD_CONNECT_TIMEOUT="${OV_DOWNLOAD_CONNECT_TIMEOUT:-10}"
OV_DOWNLOAD_MAX_TIME="${OV_DOWNLOAD_MAX_TIME:-120}"
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
  OV_MEMORY_REPO         Override GitHub repo (default: LinQiang391/OpenViking)
  OV_MEMORY_DEFAULT_REF  Fallback ref when OV_MEMORY_VERSION is not set (default: setup)
  AUTO_INSTALL_NODE      Auto-install Node.js when missing/too old (default: 1)
  AUTO_INSTALL_OPENCLAW  Auto-install OpenClaw when missing (default: 1)
  AUTO_INSTALL_BUILD_TOOLS  Auto-install cmake/g++ when missing, no prompt (default: 0)
  AUTO_INSTALL_PYTHON    Auto-install Python >=3.10 when missing/too old (default: 1)
  AUTO_INSTALL_XPM       Try xpm for missing deps before other methods (default: 1)
  ALLOW_SUDO_INSTALL     Allow sudo-based dependency install (default: 0)
  AUTO_INSTALL_MICROMAMBA  Use micromamba user env as fallback (default: 1)
  OV_MEMORY_MM_ENV       micromamba environment path (default: ~/.openviking-installer-env)
  OV_MICROMAMBA_URL      Override micromamba download URL
  OV_MICROMAMBA_CHANNEL  micromamba channel (default: conda-forge)
  OV_MICROMAMBA_CREATE_TIMEOUT  timeout seconds for toolchain create (default: 1800)
  OV_MICROMAMBA_PROGRESS_ESTIMATE  progress estimate seconds (default: 600)
  OV_PREFER_CN_MIRROR    Prefer China mirrors for nvm/node downloads (default: 1)
  OV_NVM_INSTALL_URL     Override nvm install script URL directly
  OV_NODE_VERSION        Node version for direct user install fallback (default: 22.22.0)
  OV_ALLOW_NVM_FALLBACK  Allow nvm fallback when direct node install fails (default: 0)
  USE_MIRROR             Use npmmirror for npm when installing OpenClaw (default: 1)
  OV_MEMORY_NODE_VERSION Node.js major/minor used by auto-install (default: 22)
  OV_DOWNLOAD_RETRY      curl retry count for helper download (default: 3)
  OV_DOWNLOAD_CONNECT_TIMEOUT  curl connect timeout seconds (default: 10)
  OV_DOWNLOAD_MAX_TIME   curl max time seconds per request (default: 120)
  OPENVIKING_GITHUB_RAW  Override raw base URL used by helper and installer
  SKIP_CHECKSUM=1        Skip SHA256 checksum verification
  SKIP_BUILD_TOOLS_CHECK=1  Skip cmake/g++ check (use when tools are in PATH from conda etc.)

All other arguments are forwarded to the setup helper.
EOF
}

log() { printf '[openviking-installer] %s\n' "$*"; }
warn() { printf '[openviking-installer] WARN: %s\n' "$*"; }
die() { printf '[openviking-installer] ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

sanitize_proxy_env() {
  local var val
  for var in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; do
    val="${!var:-}"
    [[ -z "$val" ]] && continue
    case "$val" in
      http://*|https://*|socks5://*|socks5h://*)
        ;;
      *)
        warn "Proxy env $var has no scheme, auto-prepending http://"
        export "$var"="http://$val"
        ;;
    esac
  done
}

run_with_progress() {
  local label="$1"
  local estimate_seconds="$2"
  shift 2

  if [[ ! -t 1 ]]; then
    "$@"
    return $?
  fi

  local width=30 elapsed=0 percent=0 filled=0
  local bar fill pad logfile pid rc
  logfile="$(mktemp)"

  (
    "$@" >"$logfile" 2>&1
  ) &
  pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    if [[ "$estimate_seconds" -gt 0 ]]; then
      percent=$(( elapsed * 100 / estimate_seconds ))
      [[ "$percent" -gt 99 ]] && percent=99
    else
      percent=0
    fi
    filled=$(( percent * width / 100 ))

    fill="$(printf '%*s' "$filled" '')"
    fill="${fill// /#}"
    pad="$(printf '%*s' "$((width - filled))" '')"
    bar="${fill}${pad}"

    printf '\r[openviking-installer] %s [%s] %3d%% (%ds)' "$label" "$bar" "$percent" "$elapsed"
    sleep 1
    elapsed=$((elapsed + 1))
  done

  wait "$pid"
  rc=$?
  if [[ "$rc" -eq 0 ]]; then
    printf '\r[openviking-installer] %s [%s] 100%% (%ds)\n' "$label" "$(printf '%*s' "$width" '' | tr ' ' '#')" "$elapsed"
    rm -f "$logfile"
    return 0
  fi

  printf '\n'
  warn "${label} failed (exit ${rc}), showing last logs:"
  tail -n 30 "$logfile" >&2 || true
  rm -f "$logfile"
  return "$rc"
}

curl_download() {
  local url="$1" out="$2"
  curl -fL \
    --retry "$OV_DOWNLOAD_RETRY" \
    --retry-delay 2 \
    --retry-all-errors \
    --connect-timeout "$OV_DOWNLOAD_CONNECT_TIMEOUT" \
    --max-time "$OV_DOWNLOAD_MAX_TIME" \
    -o "$out" "$url"
}

node_major_version() {
  node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'
}

python_cmd() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  return 1
}

python_major_minor() {
  local py="$1"
  "$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true
}

python_is_compatible() {
  local py="$1"
  local ver major minor
  ver="$(python_major_minor "$py")"
  [[ -n "$ver" ]] || return 1
  major="${ver%%.*}"
  minor="${ver##*.}"
  [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 && "$minor" -ge 10 ]]; }
}

run_xpm_install_candidates() {
  local package_name="$1"
  shift || true
  local args
  for args in "$@"; do
    if xpm $args >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

install_python_with_xpm() {
  command -v xpm >/dev/null 2>&1 || return 1
  [[ "$AUTO_INSTALL_XPM" == "1" ]] || return 1
  log "Trying to install Python >=3.10 via xpm..."

  run_xpm_install_candidates "python" \
    "install -y python@3.11" \
    "install python@3.11 -y" \
    "install -y python3.11" \
    "install python3.11 -y" \
    "install -y python" \
    "install python -y" || return 1

  return 0
}

install_build_tools_xpm() {
  command -v xpm >/dev/null 2>&1 || return 1
  [[ "$AUTO_INSTALL_XPM" == "1" ]] || return 1
  log "Trying to install build tools via xpm..."

  local cmake_ok=false cxx_ok=false
  run_xpm_install_candidates "cmake" \
    "install -y cmake" \
    "install cmake -y" && cmake_ok=true
  run_xpm_install_candidates "g++" \
    "install -y g++" \
    "install g++ -y" \
    "install -y gcc" \
    "install gcc -y" \
    "install -y cxx-compiler" \
    "install cxx-compiler -y" && cxx_ok=true

  "$cmake_ok" && "$cxx_ok"
}

ensure_micromamba() {
  if command -v micromamba >/dev/null 2>&1; then
    return 0
  fi
  [[ "$AUTO_INSTALL_MICROMAMBA" == "1" ]] || return 1

  local os url tmpdir archive
  case "$(uname -s)" in
    Linux*) os="linux-64" ;;
    Darwin*)
      if [[ "$(uname -m)" == "arm64" ]]; then
        os="osx-arm64"
      else
        os="osx-64"
      fi
      ;;
    *) return 1 ;;
  esac
  # linux aarch64
  if [[ "$os" == "linux-64" && "$(uname -m)" == "aarch64" ]]; then
    os="linux-aarch64"
  fi

  if [[ -n "$OV_MICROMAMBA_URL" ]]; then
    url="$OV_MICROMAMBA_URL"
  else
    url="https://micro.mamba.pm/api/micromamba/${os}/latest"
  fi
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/micromamba.tar.bz2"
  mkdir -p "$HOME/.local/bin"
  log "Installing micromamba (user-level)..."
  log "Downloading micromamba package..."
  if curl_download "$url" "$archive" && tar -xjf "$archive" -C "$tmpdir" >/dev/null 2>&1; then
    if [[ -f "$tmpdir/bin/micromamba" ]]; then
      cp "$tmpdir/bin/micromamba" "$HOME/.local/bin/micromamba"
      chmod +x "$HOME/.local/bin/micromamba"
      export PATH="$HOME/.local/bin:$PATH"
      rm -rf "$tmpdir"
      command -v micromamba >/dev/null 2>&1 && return 0
    fi
  fi
  rm -rf "$tmpdir"
  return 1
}

prepare_micromamba_toolchain() {
  [[ "$AUTO_INSTALL_MICROMAMBA" == "1" ]] || return 1
  ensure_micromamba || return 1

  # Reuse existing env to avoid repeated long solves/downloads.
  if [[ -x "$OV_MEMORY_MM_ENV/bin/python" && ( -x "$OV_MEMORY_MM_ENV/bin/g++" || -x "$OV_MEMORY_MM_ENV/bin/gcc" || -x "$OV_MEMORY_MM_ENV/bin/x86_64-conda-linux-gnu-g++" ) && -x "$OV_MEMORY_MM_ENV/bin/cmake" ]]; then
    log "Reusing existing micromamba toolchain env: $OV_MEMORY_MM_ENV"
  else
    log "Preparing micromamba toolchain env: $OV_MEMORY_MM_ENV"
    log "This may take several minutes on first run..."
    if command -v timeout >/dev/null 2>&1; then
      if ! run_with_progress "Setting up toolchain" "$OV_MICROMAMBA_PROGRESS_ESTIMATE" timeout "$OV_MICROMAMBA_CREATE_TIMEOUT" micromamba create -y -p "$OV_MEMORY_MM_ENV" -c "$OV_MICROMAMBA_CHANNEL" python=3.11 git cmake make gxx_linux-64; then
        run_with_progress "Setting up toolchain" "$OV_MICROMAMBA_PROGRESS_ESTIMATE" timeout "$OV_MICROMAMBA_CREATE_TIMEOUT" micromamba create -y -p "$OV_MEMORY_MM_ENV" -c "$OV_MICROMAMBA_CHANNEL" python=3.11 git cmake make cxx-compiler || return 1
      fi
    else
      if ! run_with_progress "Setting up toolchain" "$OV_MICROMAMBA_PROGRESS_ESTIMATE" micromamba create -y -p "$OV_MEMORY_MM_ENV" -c "$OV_MICROMAMBA_CHANNEL" python=3.11 git cmake make gxx_linux-64; then
        run_with_progress "Setting up toolchain" "$OV_MICROMAMBA_PROGRESS_ESTIMATE" micromamba create -y -p "$OV_MEMORY_MM_ENV" -c "$OV_MICROMAMBA_CHANNEL" python=3.11 git cmake make cxx-compiler || return 1
      fi
    fi
  fi

  export PATH="$OV_MEMORY_MM_ENV/bin:$PATH"
  if [[ -x "$OV_MEMORY_MM_ENV/bin/python" ]]; then
    export OPENVIKING_PYTHON="$OV_MEMORY_MM_ENV/bin/python"
  fi
  if [[ -d "$OV_MEMORY_MM_ENV/lib" ]]; then
    export OPENVIKING_LD_LIBRARY_PATH="$OV_MEMORY_MM_ENV/lib"
    if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
      export LD_LIBRARY_PATH="$OV_MEMORY_MM_ENV/lib:$LD_LIBRARY_PATH"
    else
      export LD_LIBRARY_PATH="$OV_MEMORY_MM_ENV/lib"
    fi
    if [[ -f "$OV_MEMORY_MM_ENV/lib/libstdc++.so.6" ]]; then
      export OPENVIKING_LD_PRELOAD="$OV_MEMORY_MM_ENV/lib/libstdc++.so.6"
      if [[ -n "${LD_PRELOAD:-}" ]]; then
        export LD_PRELOAD="$OV_MEMORY_MM_ENV/lib/libstdc++.so.6:$LD_PRELOAD"
      else
        export LD_PRELOAD="$OV_MEMORY_MM_ENV/lib/libstdc++.so.6"
      fi
    fi
  fi

  if [[ ! -x "$OV_MEMORY_MM_ENV/bin/g++" ]]; then
    if [[ -x "$OV_MEMORY_MM_ENV/bin/x86_64-conda-linux-gnu-g++" ]]; then
      ln -sf "$OV_MEMORY_MM_ENV/bin/x86_64-conda-linux-gnu-g++" "$OV_MEMORY_MM_ENV/bin/g++"
    fi
  fi
  if [[ ! -x "$OV_MEMORY_MM_ENV/bin/gcc" ]]; then
    if [[ -x "$OV_MEMORY_MM_ENV/bin/x86_64-conda-linux-gnu-gcc" ]]; then
      ln -sf "$OV_MEMORY_MM_ENV/bin/x86_64-conda-linux-gnu-gcc" "$OV_MEMORY_MM_ENV/bin/gcc"
    fi
  fi

  command -v python >/dev/null 2>&1 || return 1
  command -v git >/dev/null 2>&1 || return 1
  return 0
}

install_node_with_nvm() {
  local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  local nvm_install_url="${OV_NVM_INSTALL_URL:-https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh}"
  local nvm_install_url_cn="https://npmmirror.com/mirrors/nvm/v0.40.3/install.sh"
  local nvm_script_file
  export NVM_DIR="$nvm_dir"

  log "Installing Node.js ${OV_MEMORY_NODE_VERSION} via nvm..."
  if [[ ! -s "${nvm_dir}/nvm.sh" ]]; then
    nvm_script_file="$(mktemp)"
    if [[ "$OV_PREFER_CN_MIRROR" == "1" && -z "${OV_NVM_INSTALL_URL:-}" ]]; then
      if ! curl_download "$nvm_install_url_cn" "$nvm_script_file"; then
        warn "Failed to download nvm from China mirror, falling back to GitHub"
        curl_download "$nvm_install_url" "$nvm_script_file" || die "Failed to download nvm install script"
      fi
    else
      curl_download "$nvm_install_url" "$nvm_script_file" || die "Failed to download nvm install script"
    fi
    bash "$nvm_script_file" || die "Failed to install nvm"
    rm -f "$nvm_script_file"
  fi

  # shellcheck source=/dev/null
  [ -s "${nvm_dir}/nvm.sh" ] && . "${nvm_dir}/nvm.sh" || die "Failed to load nvm"

  # Prefer domestic mirrors for Node binary downloads when requested.
  if [[ "$OV_PREFER_CN_MIRROR" == "1" ]]; then
    export NVM_NODEJS_ORG_MIRROR="${NVM_NODEJS_ORG_MIRROR:-https://npmmirror.com/mirrors/node}"
    export NVM_IOJS_ORG_MIRROR="${NVM_IOJS_ORG_MIRROR:-https://npmmirror.com/mirrors/iojs}"
  fi

  nvm install "${OV_MEMORY_NODE_VERSION}" || die "Failed to install Node.js ${OV_MEMORY_NODE_VERSION}"
  nvm use "${OV_MEMORY_NODE_VERSION}" >/dev/null || die "Failed to activate Node.js ${OV_MEMORY_NODE_VERSION}"
}

install_node_direct_user() {
  local os arch tar_arch ext pkg node_root archive
  ext="tar.xz"
  case "$(uname -s)" in
    Linux*) os="linux" ;;
    Darwin*) os="darwin" ;;
    *) return 1 ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) return 1 ;;
  esac

  pkg="node-v${OV_NODE_VERSION}-${os}-${arch}.${ext}"
  archive="$(mktemp)"
  node_root="$HOME/.local/node-v${OV_NODE_VERSION}-${os}-${arch}"
  mkdir -p "$HOME/.local/bin"

  log "Installing Node.js ${OV_NODE_VERSION} (user-level binary)..."
  if [[ "$OV_PREFER_CN_MIRROR" == "1" ]]; then
    if ! curl_download "https://npmmirror.com/mirrors/node/v${OV_NODE_VERSION}/${pkg}" "$archive"; then
      warn "Failed to download Node from China mirror, falling back to nodejs.org"
      curl_download "https://nodejs.org/dist/v${OV_NODE_VERSION}/${pkg}" "$archive" || return 1
    fi
  else
    curl_download "https://nodejs.org/dist/v${OV_NODE_VERSION}/${pkg}" "$archive" || return 1
  fi

  rm -rf "$node_root"
  mkdir -p "$node_root"
  tar -xJf "$archive" -C "$node_root" --strip-components=1 || return 1
  rm -f "$archive"

  ln -sf "$node_root/bin/node" "$HOME/.local/bin/node"
  ln -sf "$node_root/bin/npm" "$HOME/.local/bin/npm"
  ln -sf "$node_root/bin/npx" "$HOME/.local/bin/npx"
  export PATH="$HOME/.local/bin:$PATH"
  return 0
}

ensure_xpm() {
  if command -v xpm >/dev/null 2>&1; then
    return 0
  fi

  [[ "$AUTO_INSTALL_XPM" == "1" ]] || return 1
  command -v npm >/dev/null 2>&1 || return 1

  log "xpm not found, installing xpm via npm (user-level)..."
  if npm install -g xpm >/dev/null 2>&1; then
    command -v xpm >/dev/null 2>&1 && { log "xpm is ready"; return 0; }
  fi
  return 1
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
  if ! install_node_direct_user; then
    if [[ "$OV_ALLOW_NVM_FALLBACK" == "1" ]]; then
      warn "Direct Node install failed, trying nvm fallback..."
      install_node_with_nvm
    else
      die "Direct Node install failed and nvm fallback is disabled (OV_ALLOW_NVM_FALLBACK=0). Set OV_ALLOW_NVM_FALLBACK=1 to enable nvm fallback."
    fi
  fi

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

  if prepare_micromamba_toolchain; then
    command -v cmake >/dev/null 2>&1 && { command -v g++ >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1; } && {
      log "Build tools OK via micromamba: cmake, g++"
      return
    }
  fi

  local has_sudo=false has_conda=false
  command -v sudo >/dev/null 2>&1 && has_sudo=true
  command -v conda >/dev/null 2>&1 || command -v mamba >/dev/null 2>&1 && has_conda=true
  local has_xpm=false
  command -v xpm >/dev/null 2>&1 && has_xpm=true

  if ! "$has_sudo" && ! "$has_conda" && ! "$has_xpm"; then
    build_tools_manual_help
    die "No sudo/conda/xpm available. Install cmake and g++ manually, or use conda/xpm. Set SKIP_BUILD_TOOLS_CHECK=1 if already installed."
  fi

  local install_it=0 method=""
  if [[ "$AUTO_INSTALL_XPM" == "1" ]] && "$has_xpm"; then
    method="xpm"
    install_it=1
  fi
  if [[ "$AUTO_INSTALL_BUILD_TOOLS" == "1" ]]; then
    if [[ -z "$method" ]] && command -v sudo >/dev/null 2>&1; then
      if [[ "$ALLOW_SUDO_INSTALL" == "1" ]]; then
        method="sudo"
        install_it=1
      fi
    elif [[ -z "$method" ]] && "$has_conda"; then
      method="conda"
      install_it=1
    fi
  elif [[ -t 1 ]] && [[ -c /dev/tty ]] 2>/dev/null; then
    local resp user_choice=""
    printf '[openviking-installer] Build tools missing: %s.\n' "${missing[*]}"
    if "$has_xpm"; then
      printf '  [x] Install via xpm (no sudo, user-level)\n'
    fi
    if "$has_sudo"; then
      printf '  [s] Install via sudo (needs root password)\n'
    fi
    if "$has_conda"; then
      printf '  [c] Install via conda (no sudo, user-level)\n'
    else
      printf '  [c] Install via conda (install Miniconda first if not available)\n'
    fi
    printf '  [n] Skip, show manual instructions\n'
    printf '  Choice [x/s/c/n]: '
    read -r resp </dev/tty 2>/dev/null || true
    case "${resp:-x}" in
      [xX]) "$has_xpm" && method="xpm" && install_it=1 ; user_choice="x" ;;
      [sS]) "$has_sudo" && method="sudo"  && install_it=1 ; user_choice="s" ;;
      [cC]) user_choice="c"
            if "$has_conda"; then
              method="conda" && install_it=1
            else
              install_it=0
            fi ;;
      *)    install_it=0 ; user_choice="n" ;;
    esac
  fi

  if [[ "$install_it" -eq 1 ]] && [[ -n "$method" ]]; then
    local ok=false
    if [[ "$method" == "xpm" ]] && "$has_xpm"; then
      install_build_tools_xpm && ok=true
    elif [[ "$method" == "sudo" ]] && "$has_sudo"; then
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
      if ! "$has_conda"; then
        die "Install failed. Try xpm or conda, or ask your admin to install cmake and g++ without root."
      else
        die "Failed to install build tools. See instructions above."
      fi
    fi
  else
    build_tools_manual_help
    if [[ "${user_choice:-}" == "x" ]] && ! "$has_xpm"; then
      die "xpm not found. Install xpm first, or use sudo/conda/manual installation."
    elif [[ "${user_choice:-}" == "c" ]] && ! "$has_conda"; then
      die "Conda not found. Install Miniconda first (no sudo):
  curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
  bash /tmp/miniconda.sh -b -p ~/miniconda3
  ~/miniconda3/bin/conda init
  source ~/.bashrc
  Then run this script again and choose [c]."
    elif [[ "${user_choice:-}" == "s" ]] && ! "$has_sudo"; then
      die "Sudo not available. Use conda (option c) or ask your admin to install cmake and g++."
    else
      die "Run again and choose an option, or install manually, or set SKIP_BUILD_TOOLS_CHECK=1."
    fi
  fi
}

install_python_sudo() {
  log "Installing Python >=3.10 via package manager (sudo required)..."
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-devel python3-pip || return 1
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 python3-devel python3-pip || return 1
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y python3 python3-dev python3-pip || return 1
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper -n install python3 python3-devel python3-pip || return 1
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add --no-cache python3 py3-pip python3-dev || return 1
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python python-pip || return 1
  elif command -v brew >/dev/null 2>&1; then
    brew install python || return 1
  else
    return 1
  fi
}

install_python_conda() {
  local conda_cmd=""
  command -v mamba >/dev/null 2>&1 && conda_cmd="mamba"
  command -v conda >/dev/null 2>&1 && [[ -z "$conda_cmd" ]] && conda_cmd="conda"
  [[ -z "$conda_cmd" ]] && return 1
  log "Installing Python >=3.10 via $conda_cmd (no sudo)..."
  $conda_cmd install -y -c conda-forge "python>=3.10" 2>/dev/null && return 0
  $conda_cmd install -y "python>=3.10" 2>/dev/null && return 0
  return 1
}

ensure_python() {
  local py
  py="$(python_cmd || true)"
  if [[ -n "$py" ]] && python_is_compatible "$py"; then
    export OPENVIKING_PYTHON="$py"
    log "Detected Python: $("$py" --version 2>&1)"
    return
  fi

  [[ "$AUTO_INSTALL_PYTHON" == "1" ]] || die "Python >=3.10 is required. Set AUTO_INSTALL_PYTHON=1 or install Python manually."
  log "Python >=3.10 is missing or too old"

  if install_python_with_xpm; then
    py="$(python_cmd || true)"
    if [[ -n "$py" ]] && python_is_compatible "$py"; then
      export OPENVIKING_PYTHON="$py"
      log "Python ready after xpm install: $("$py" --version 2>&1)"
      return
    fi
  fi

  if install_python_conda; then
    py="$(python_cmd || true)"
    if [[ -n "$py" ]] && python_is_compatible "$py"; then
      export OPENVIKING_PYTHON="$py"
      log "Python ready: $("$py" --version 2>&1)"
      return
    fi
  fi

  if prepare_micromamba_toolchain; then
    py="$(python_cmd || true)"
    if [[ -n "$py" ]] && python_is_compatible "$py"; then
      export OPENVIKING_PYTHON="$py"
      log "Python ready via micromamba: $("$py" --version 2>&1)"
      return
    fi
  fi

  if [[ "$ALLOW_SUDO_INSTALL" == "1" ]] && install_python_sudo; then
    py="$(python_cmd || true)"
    if [[ -n "$py" ]] && python_is_compatible "$py"; then
      export OPENVIKING_PYTHON="$py"
      log "Python ready: $("$py" --version 2>&1)"
      return
    fi
  fi

  die "Failed to provision Python >=3.10 with user-level methods (xpm/conda). Install Python without root or set ALLOW_SUDO_INSTALL=1 if you explicitly allow sudo."
}

ensure_git() {
  if command -v git >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$AUTO_INSTALL_XPM" == "1" ]] && command -v xpm >/dev/null 2>&1; then
    run_xpm_install_candidates "git" \
      "install -y git" \
      "install git -y" && command -v git >/dev/null 2>&1 && return 0
  fi

  prepare_micromamba_toolchain && command -v git >/dev/null 2>&1 && return 0

  die "git is required for npm dependency resolution, and could not be provisioned with user-level methods."
}

ensure_openclaw() {
  if command -v openclaw >/dev/null 2>&1; then
    log "Detected OpenClaw: $(openclaw --version 2>/dev/null || openclaw -v 2>/dev/null || echo 'installed')"
    return
  fi

  [[ "$AUTO_INSTALL_OPENCLAW" == "1" ]] || die "OpenClaw is not installed. Install it first: npm install -g openclaw"
  ensure_git

  log "OpenClaw is not installed. Installing via npm..."
  local npm_opts=(-g openclaw)
  [[ "$USE_MIRROR" == "1" ]] && npm_opts+=(--registry="$NPM_REGISTRY_MIRROR")
  if ! npm install "${npm_opts[@]}"; then
    warn "npm install failed. Retrying once with proxy env cleared..."
    if ! env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY npm install "${npm_opts[@]}"; then
      die "Failed to install OpenClaw. Run 'npm install -g openclaw' manually."
    fi
  fi

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
sanitize_proxy_env
ensure_node
ensure_xpm || true
ensure_python
ensure_build_tools
ensure_git
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
curl_download "$HELPER_URL" "$HELPER_PATH" || die "Failed to download helper: $HELPER_URL"

if [[ "$SKIP_CHECKSUM" != "1" ]]; then
  log "Downloading checksum..."
  if curl_download "$CHECKSUM_URL" "$CHECKSUM_PATH"; then
    EXPECTED="$(awk 'NF { print $1; exit }' "$CHECKSUM_PATH" | tr '[:upper:]' '[:lower:]')"
    if [[ -z "$EXPECTED" ]]; then
      warn "Checksum file is empty, skipping verification: $CHECKSUM_URL"
    else
      if command -v sha256sum >/dev/null 2>&1; then
        ACTUAL="$(sha256sum "$HELPER_PATH" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
      elif command -v shasum >/dev/null 2>&1; then
        ACTUAL="$(shasum -a 256 "$HELPER_PATH" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
      else
        warn "No SHA256 tool found (sha256sum/shasum), skipping verification"
        ACTUAL=""
      fi

      if [[ -n "$ACTUAL" ]]; then
        [[ "$ACTUAL" == "$EXPECTED" ]] || die "Checksum mismatch for setup helper"
        log "Checksum verification passed"
      fi
    fi
  else
    warn "Failed to download checksum, skipping verification: $CHECKSUM_URL"
  fi
else
  log "Skipping checksum verification (SKIP_CHECKSUM=1)"
fi

cat >"$PKG_PATH" <<'EOF'
{"type":"module"}
EOF

log "Running setup helper..."
if [[ -r /dev/tty ]]; then
  node "$HELPER_PATH" "${HELPER_ARGS[@]}" </dev/tty
else
  node "$HELPER_PATH" "${HELPER_ARGS[@]}"
fi
