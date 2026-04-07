"""
OpenClaw Gateway deployment script.

Generates a complete, immediately usable openclaw.json (with model providers,
auth, workspace, and plugin settings), creates all required directories, then
optionally starts the gateway.

Each profile gets a fully isolated state directory:
  default  -> ~/.openclaw/
  second   -> ~/.openclaw-second/
  <name>   -> ~/.openclaw-<name>/

Prerequisites:
  - openclaw installed via npm (globally available on PATH)
  - openviking installed via pip (if using openviking plugin)
  - openviking server already running (if using openviking plugin in remote mode)

Quick start:
  # Env-1: OpenViking + volcengine models (deploy and start)
  python deploy_gateway.py

  # Env-2: pure OpenClaw, no plugins, second profile
  python deploy_gateway.py --profile second --port 18890 --no-openviking

  # Import model config from an existing openclaw.json
  python deploy_gateway.py --models-from ~/.openclaw/openclaw.json

  # Dry-run: print config without writing
  python deploy_gateway.py --dry-run
"""

import argparse
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Defaults & Constants ────────────────────────────────────────────────────

OPENVIKING_PLUGIN_DIR = str(
    Path(__file__).resolve().parent.parent
)

DEFAULT_OV_CONFIG: dict[str, Any] = {
    "mode": "remote",
    "baseUrl": "http://127.0.0.1:1933",
    "autoCapture": True,
    "autoRecall": True,
    "emitStandardDiagnostics": True,
    "logFindRequests": True,
}

# Built-in model provider presets.
# Each preset is a dict ready to be placed under models.providers.<id>.
MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "volcengine-plan": {
        "baseUrl": "https://ark.cn-beijing.volces.com/api/coding",
        "api": "anthropic-messages",
        "models": [
            {
                "id": "doubao-seed-2-0-code-preview-260215",
                "name": "doubao-seed-2-0-code-preview-260215",
                "reasoning": False,
                "input": ["text"],
                "cost": {"input": 0.0001, "output": 0.0002, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 256000,
                "maxTokens": 4096,
            }
        ],
    },
    "zai": {
        "baseUrl": "https://open.bigmodel.cn/api/coding/paas/v4",
        "api": "openai-completions",
        "models": [
            {
                "id": "glm-5",
                "name": "GLM-5",
                "reasoning": True,
                "input": ["text"],
                "cost": {"input": 1, "output": 3.2, "cacheRead": 0.2, "cacheWrite": 0},
                "contextWindow": 202800,
                "maxTokens": 131100,
            },
            {
                "id": "glm-4.7",
                "name": "GLM-4.7",
                "reasoning": True,
                "input": ["text"],
                "cost": {"input": 0.6, "output": 2.2, "cacheRead": 0.11, "cacheWrite": 0},
                "contextWindow": 204800,
                "maxTokens": 131072,
            },
            {
                "id": "glm-4.7-flash",
                "name": "GLM-4.7 Flash",
                "reasoning": True,
                "input": ["text"],
                "cost": {"input": 0.07, "output": 0.4, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 200000,
                "maxTokens": 131072,
            },
        ],
    },
}

DEFAULT_PRIMARY_MODEL = "volcengine-plan/doubao-seed-2-0-code-preview-260215"

DEFAULT_AGENT_MODELS: dict[str, dict] = {
    "volcengine-plan/doubao-seed-2-0-code-preview-260215": {"alias": "Doubao Seed 2.0 Code Preview"},
    "zai/glm-5": {"alias": "GLM"},
    "zai/glm-4.7": {},
    "zai/glm-4.7-flash": {},
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def resolve_state_dir(profile: str) -> Path:
    home = Path.home()
    if profile == "default":
        return home / ".openclaw"
    return home / f".openclaw-{profile}"


def generate_auth_token() -> str:
    return secrets.token_hex(24)



def ensure_directories(state_dir: Path) -> list[Path]:
    """Create all required subdirectories for an OpenClaw profile.
    Returns list of created directories."""
    dirs = [
        state_dir,
        state_dir / "agents" / "main" / "agent",
        state_dir / "agents" / "main" / "sessions",
        state_dir / "workspace",
        state_dir / "memory",
        state_dir / "logs",
        state_dir / "devices",
        state_dir / "identity",
        state_dir / "canvas",
    ]
    created = []
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)
    return created


# ── Config builder ──────────────────────────────────────────────────────────

def build_models_config(
    *,
    api_keys: dict[str, str],
) -> dict[str, Any]:
    """Build the top-level 'models' section with provider presets."""
    providers: dict[str, Any] = {}
    for provider_id, preset in MODEL_PRESETS.items():
        p = copy.deepcopy(preset)
        key = api_keys.get(provider_id, "")
        if key:
            p["apiKey"] = key
        providers[provider_id] = p
    return {"mode": "merge", "providers": providers}


def build_auth_config(api_keys: dict[str, str]) -> dict[str, Any]:
    """Build auth.profiles from known providers that need auth."""
    profiles: dict[str, Any] = {}
    if api_keys.get("zai"):
        profiles["zai:default"] = {"provider": "zai", "mode": "api_key"}
    return {"profiles": profiles} if profiles else {}


def build_config(
    *,
    profile: str,
    port: int,
    auth_token: str,
    enable_openviking: bool,
    enable_mem_core: bool,
    ov_mode: str,
    ov_base_url: str,
    ov_api_key: str,
    ov_agent_id: str,
    plugin_dir: str,
    primary_model: str,
    api_keys: dict[str, str],
    existing_config: Optional[dict] = None,
) -> dict[str, Any]:
    """Build a complete, immediately usable openclaw.json."""

    config: dict[str, Any] = existing_config or {}
    state_dir = resolve_state_dir(profile)

    # ── Models (only if not already present or no existing config) ────────
    if "models" not in config:
        config["models"] = build_models_config(api_keys=api_keys)
    else:
        # Patch API keys into existing providers if provided
        for pid, key in api_keys.items():
            if key and pid in config.get("models", {}).get("providers", {}):
                config["models"]["providers"][pid]["apiKey"] = key

    # ── Auth ─────────────────────────────────────────────────────────────
    if "auth" not in config:
        auth = build_auth_config(api_keys)
        if auth:
            config["auth"] = auth

    # ── Gateway ──────────────────────────────────────────────────────────
    gw = config.setdefault("gateway", {})
    gw["port"] = port
    gw["mode"] = "local"
    gw["bind"] = "loopback"
    gw["auth"] = {"mode": "token", "token": auth_token}
    gw.setdefault("http", {}).setdefault("endpoints", {})
    gw["http"]["endpoints"].setdefault("chatCompletions", {"enabled": True})
    gw["http"]["endpoints"].setdefault("responses", {"enabled": True})

    # ── Agents ───────────────────────────────────────────────────────────
    agents = config.setdefault("agents", {})
    defaults = agents.setdefault("defaults", {})
    defaults["workspace"] = str(state_dir / "workspace")
    defaults.setdefault("model", {})["primary"] = primary_model
    if "models" not in defaults:
        defaults["models"] = copy.deepcopy(DEFAULT_AGENT_MODELS)
    if "list" not in agents:
        agents["list"] = [{"id": "main"}]

    # ── Tools ────────────────────────────────────────────────────────────
    config.setdefault("tools", {"profile": "coding"})

    # ── Commands ─────────────────────────────────────────────────────────
    config.setdefault("commands", {"native": "auto", "nativeSkills": "auto", "restart": True})

    # ── Session ──────────────────────────────────────────────────────────
    config.setdefault("session", {"dmScope": "per-channel-peer"})

    # ── Plugins ──────────────────────────────────────────────────────────
    any_plugin = enable_openviking or enable_mem_core
    plugins: dict[str, Any] = {"enabled": any_plugin}

    if any_plugin:
        allow_list = []
        if enable_mem_core:
            allow_list.append("memory-core")
        if enable_openviking:
            allow_list.append("openviking")
        plugins["allow"] = allow_list

        if enable_openviking:
            load_path = str(resolve_state_dir(profile) / "extensions")
            plugins["load"] = {"paths": [load_path]}

        plugins["slots"] = {
            "memory": "memory-core" if enable_mem_core else "none",
            "contextEngine": "openviking" if enable_openviking else "legacy",
        }

        entries: dict[str, Any] = {}
        entries["memory-core"] = {"enabled": enable_mem_core}
        entries["memory-lancedb"] = {"enabled": False}
        if enable_openviking:
            ov_cfg = dict(DEFAULT_OV_CONFIG)
            ov_cfg["mode"] = ov_mode
            ov_cfg["baseUrl"] = ov_base_url
            if ov_api_key:
                ov_cfg["apiKey"] = ov_api_key
            if ov_agent_id:
                ov_cfg["agentId"] = ov_agent_id
            entries["openviking"] = {"enabled": True, "config": ov_cfg}
        plugins["entries"] = entries

    config["plugins"] = plugins
    return config


# ── Config I/O ──────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(config: dict[str, Any], config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def import_models_from(source_path: str) -> dict[str, Any]:
    """Import models, auth, and agent model settings from an existing openclaw.json."""
    src = load_config(Path(source_path))
    imported: dict[str, Any] = {}
    for key in ("models", "auth"):
        if key in src:
            imported[key] = copy.deepcopy(src[key])
    # Also import agent model defaults
    agent_defaults = src.get("agents", {}).get("defaults", {})
    if "model" in agent_defaults or "models" in agent_defaults:
        imported.setdefault("agents", {}).setdefault("defaults", {})
        if "model" in agent_defaults:
            imported["agents"]["defaults"]["model"] = copy.deepcopy(agent_defaults["model"])
        if "models" in agent_defaults:
            imported["agents"]["defaults"]["models"] = copy.deepcopy(agent_defaults["models"])
    return imported


# ── Gateway process ─────────────────────────────────────────────────────────

def detect_repo_root() -> Optional[Path]:
    """Walk up from this script to find the OpenViking repo root (has examples/openclaw-plugin)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "examples" / "openclaw-plugin" / "openclaw.plugin.json").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def install_plugin(*, profile: str, plugin_dir: str) -> bool:
    """Install the OpenViking plugin via ov-install."""
    ov_install = shutil.which("ov-install")
    if not ov_install:
        print("WARNING: 'ov-install' not found on PATH. Plugin not installed.", file=sys.stderr)
        print("  Install manually: SKIP_OPENVIKING=1 ov-install --workdir <state_dir> -y")
        return False

    state_dir = resolve_state_dir(profile)
    ext_dir = state_dir / "extensions"
    has_existing = any(ext_dir.glob("*openviking*")) if ext_dir.exists() else False

    cmd = [ov_install, "--workdir", str(state_dir), "-y"]
    if has_existing:
        cmd.insert(1, "--update")

    env = os.environ.copy()
    env["SKIP_OPENVIKING"] = "1"

    ov_conf = Path.home() / ".openviking" / "ov.conf"
    ov_conf_backup = None
    if ov_conf.exists():
        ov_conf_backup = ov_conf.read_bytes()

    mode_label = "update" if has_existing else "fresh install"
    print(f"  Installing plugin via ov-install ({mode_label}, SKIP_OPENVIKING=1):")
    print(f"  CMD: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, env=env, timeout=300)
        if proc.returncode == 0:
            print(f"  Plugin installed. Config will be managed by this script.")
        else:
            print(f"  ov-install exited with code {proc.returncode}")
    except subprocess.TimeoutExpired:
        print("  ov-install timed out (300s).")
    except Exception as e:
        print(f"  ov-install error: {e}")

    if ov_conf_backup is not None:
        current = ov_conf.read_bytes() if ov_conf.exists() else b""
        if current != ov_conf_backup:
            ov_conf.write_bytes(ov_conf_backup)
            print(f"  Restored ov.conf (ov-install modified it)")

    return True


_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tee_to_jsonl(proc: subprocess.Popen, log_path: Path, *, quiet: bool = False) -> None:
    """Read gateway stdout and append as JSONL to *log_path*.

    When *quiet* is False (default), lines are also echoed to the console.
    """
    with open(log_path, "a", encoding="utf-8") as fh:
        for raw_line in iter(proc.stdout.readline, ""):
            if not quiet:
                try:
                    sys.stdout.write(raw_line)
                except UnicodeEncodeError:
                    sys.stdout.write(raw_line.encode("ascii", errors="replace").decode("ascii"))
                sys.stdout.flush()
            cleaned = _ANSI_ESCAPE_RE.sub("", raw_line).strip()
            if not cleaned:
                continue
            record = {"time": _utc_now_iso(), "1": cleaned}
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()


def start_gateway(
    *,
    profile: str,
    port: int,
    verbose: bool,
    cache_trace: bool,
    trace_dir: Optional[Path],
) -> int:
    openclaw_exe = shutil.which("openclaw")
    if not openclaw_exe:
        print("ERROR: 'openclaw' not found on PATH.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.pop("OPENCLAW_HOME", None)
    env.pop("OPENCLAW_STATE_DIR", None)

    if cache_trace:
        if trace_dir is None:
            trace_dir = resolve_state_dir(profile) / "logs" / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        env["OPENCLAW_CACHE_TRACE"] = "1"
        env["OPENCLAW_CACHE_TRACE_FILE"] = str(trace_dir / "cache-trace.jsonl")
        env["OPENCLAW_CACHE_TRACE_MESSAGES"] = "1"
        env["OPENCLAW_CACHE_TRACE_PROMPT"] = "1"
        env["OPENCLAW_CACHE_TRACE_SYSTEM"] = "1"
        env["OPENVIKING_DIAGNOSTICS_PATH"] = str(trace_dir / "openviking-diagnostics.jsonl")
        print(f"  Cache-trace -> {trace_dir}")

    cmd = [openclaw_exe]
    if profile != "default":
        cmd.extend(["--profile", profile])
    cmd.extend(["gateway", "--port", str(port), "--bind", "loopback"])

    if verbose:
        cmd.append("--verbose")

    print(f"\n  CMD: {' '.join(cmd)}\n")

    try:
        if cache_trace and trace_dir is not None:
            gateway_log = trace_dir / "gateway.log.jsonl"
            proc = subprocess.Popen(
                cmd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            tee_thread = threading.Thread(
                target=_tee_to_jsonl, args=(proc, gateway_log), daemon=True,
            )
            tee_thread.start()
            proc.wait()
            tee_thread.join(timeout=2)
            return proc.returncode
        else:
            proc = subprocess.run(cmd, env=env)
            return proc.returncode
    except KeyboardInterrupt:
        print("\nGateway stopped.")
        return 0


# ── Summary ─────────────────────────────────────────────────────────────────

def print_summary(config: dict, config_path: Path, profile: str, created_dirs: list[Path]) -> None:
    plugins = config.get("plugins", {})
    slots = plugins.get("slots", {})
    entries = plugins.get("entries", {})
    gw = config.get("gateway", {})
    agent_defaults = config.get("agents", {}).get("defaults", {})

    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  OpenClaw Gateway Deployment Summary")
    print(sep)
    print(f"  Profile         : {profile}")
    print(f"  State dir       : {config_path.parent}")
    print(f"  Config file     : {config_path}")
    print(f"  Workspace       : {agent_defaults.get('workspace', '?')}")
    print(f"  Gateway port    : {gw.get('port', '?')}")
    print(f"  Auth token      : {gw.get('auth', {}).get('token', 'none')}")
    print(f"  Primary model   : {agent_defaults.get('model', {}).get('primary', '?')}")

    # Model providers
    providers = config.get("models", {}).get("providers", {})
    if providers:
        names = ", ".join(providers.keys())
        print(f"  Model providers : {names}")

    print(f"  Plugins         : {'enabled' if plugins.get('enabled') else 'disabled'}")
    if plugins.get("enabled"):
        print(f"    memory slot   : {slots.get('memory', 'default')}")
        print(f"    contextEngine : {slots.get('contextEngine', 'default')}")
        if entries.get("openviking", {}).get("enabled"):
            ov_cfg = entries["openviking"].get("config", {})
            print(f"    OpenViking    : mode={ov_cfg.get('mode')}, url={ov_cfg.get('baseUrl')}")
        else:
            print(f"    OpenViking    : disabled")
        print(f"    memory-core   : {'enabled' if entries.get('memory-core', {}).get('enabled') else 'disabled'}")

    if created_dirs:
        print(f"  New directories : {len(created_dirs)} created")

    print(sep)
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Deploy a complete, immediately usable OpenClaw gateway environment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default env with OpenViking
  python deploy_gateway.py

  # Second env, no plugins
  python deploy_gateway.py --profile second --port 18890 --no-openviking

  # Import models from existing config
  python deploy_gateway.py --models-from ~/.openclaw/openclaw.json --profile test --port 19000

  # OpenViking + mem-core, with cache-trace
  python deploy_gateway.py --mem-core --cache-trace

  # Only generate config, don't start
  python deploy_gateway.py --config-only
""",
    )

    # ── Profile & gateway ────────────────────────────────────────────────
    g_gw = parser.add_argument_group("Profile & Gateway")
    g_gw.add_argument("--profile", default="default", help="Profile name for isolation (default/second/<custom>)")
    g_gw.add_argument("--port", type=int, default=None, help="Gateway port (auto: 18789 for default, 18890 for second, 19000+ for custom)")
    g_gw.add_argument("--auth-token", default=None, help="Gateway auth token (auto-generated if omitted)")

    # ── Models ───────────────────────────────────────────────────────────
    g_model = parser.add_argument_group("Model configuration")
    g_model.add_argument("--models-from", default=None, metavar="PATH", help="Import models/auth from an existing openclaw.json")
    g_model.add_argument("--primary-model", default=None, help=f"Primary model ID (default: {DEFAULT_PRIMARY_MODEL})")
    g_model.add_argument("--volcengine-key", default=None, help="Volcengine API key (or env VOLCENGINE_API_KEY)")
    g_model.add_argument("--zai-key", default=None, help="ZAI (Zhipu) API key (or env ZAI_API_KEY)")

    # ── Plugins ──────────────────────────────────────────────────────────
    g_plug = parser.add_argument_group("Plugin settings")
    g_plug.add_argument("--openviking", action="store_true", default=True, dest="openviking", help="Enable OpenViking plugin (default)")
    g_plug.add_argument("--no-openviking", action="store_false", dest="openviking", help="Disable OpenViking plugin")
    g_plug.add_argument("--mem-core", action="store_true", default=False, help="Enable memory-core plugin")
    g_plug.add_argument("--no-mem-core", action="store_false", dest="mem_core", help="Disable memory-core (default)")

    # ── OpenViking ───────────────────────────────────────────────────────
    g_ov = parser.add_argument_group("OpenViking plugin config")
    g_ov.add_argument("--ov-mode", default="remote", choices=["local", "remote"], help="Connection mode (default: remote)")
    g_ov.add_argument("--ov-url", default="http://127.0.0.1:1933", help="OpenViking server URL")
    g_ov.add_argument("--ov-api-key", default=None, help="OpenViking API key (required when --openviking is enabled)")
    g_ov.add_argument("--ov-agent-id", default=None, help="OpenViking agent ID prefix (default: profile name, e.g. 'default', 'second')")
    g_ov.add_argument("--plugin-dir", default=OPENVIKING_PLUGIN_DIR, help="Path to openclaw-plugin source directory")

    # ── Behavior ─────────────────────────────────────────────────────────
    g_behav = parser.add_argument_group("Behavior")
    g_behav.add_argument("--cache-trace", action="store_true", help="Enable cache-trace capture")
    g_behav.add_argument("--trace-dir", default=None, help="Cache-trace output directory")
    g_behav.add_argument("--verbose", action="store_true", help="Verbose gateway output")
    g_behav.add_argument("--config-only", action="store_true", help="Write config only, don't start gateway")
    g_behav.add_argument("--dry-run", action="store_true", help="Print config to stdout, don't write")
    g_behav.add_argument("--merge", action="store_true", help="Merge into existing config (keep models/auth)")
    g_behav.add_argument("--reset", action="store_true", help="Delete existing config before generating")

    args = parser.parse_args()

    # ── Resolve defaults ─────────────────────────────────────────────────
    if args.port is None:
        if args.profile == "default":
            args.port = 18789
        elif args.profile == "second":
            args.port = 18890
        else:
            args.port = 19000

    if args.openviking and not args.ov_api_key:
        print("ERROR: --ov-api-key is required when OpenViking is enabled.", file=sys.stderr)
        print("  Usage: --ov-api-key <your-openviking-api-key>")
        sys.exit(1)
    if args.ov_api_key is None:
        args.ov_api_key = ""
    if args.ov_agent_id is None:
        args.ov_agent_id = args.profile

    state_dir = resolve_state_dir(args.profile)
    config_path = state_dir / "openclaw.json"

    # ── Existing config handling ─────────────────────────────────────────
    existing_config: Optional[dict] = None
    if args.reset and config_path.exists():
        print(f"Resetting: removing {config_path}")
        config_path.unlink()
    elif args.merge and config_path.exists():
        existing_config = load_config(config_path)
        print(f"Merging into: {config_path}")

    # ── Models import ────────────────────────────────────────────────────
    if args.models_from:
        src_path = Path(args.models_from).expanduser()
        if not src_path.exists():
            print(f"ERROR: --models-from file not found: {src_path}", file=sys.stderr)
            sys.exit(1)
        imported = import_models_from(str(src_path))
        if existing_config is None:
            existing_config = {}
        # Deep merge imported models/auth
        for key, value in imported.items():
            if key == "agents":
                existing_config.setdefault("agents", {}).setdefault("defaults", {})
                for dk, dv in value.get("defaults", {}).items():
                    existing_config["agents"]["defaults"][dk] = dv
            else:
                existing_config[key] = value
        print(f"Imported models/auth from: {src_path}")

    # ── API keys (env fallback) ──────────────────────────────────────────
    api_keys: dict[str, str] = {}
    api_keys["volcengine-plan"] = args.volcengine_key or os.environ.get("VOLCENGINE_API_KEY", "")
    api_keys["zai"] = args.zai_key or os.environ.get("ZAI_API_KEY", "")

    # ── Auth token ───────────────────────────────────────────────────────
    auth_token = args.auth_token
    if not auth_token:
        if existing_config:
            auth_token = existing_config.get("gateway", {}).get("auth", {}).get("token")
        if not auth_token:
            auth_token = generate_auth_token()

    primary_model = args.primary_model or DEFAULT_PRIMARY_MODEL

    # ── Build config ─────────────────────────────────────────────────────
    config = build_config(
        profile=args.profile,
        port=args.port,
        auth_token=auth_token,
        enable_openviking=args.openviking,
        enable_mem_core=args.mem_core,
        ov_mode=args.ov_mode,
        ov_base_url=args.ov_url,
        ov_api_key=args.ov_api_key,
        ov_agent_id=args.ov_agent_id,
        plugin_dir=args.plugin_dir,
        primary_model=primary_model,
        api_keys=api_keys,
        existing_config=existing_config,
    )

    # ── Dry-run ──────────────────────────────────────────────────────────
    if args.dry_run:
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return

    # ── Create directories ───────────────────────────────────────────────
    created_dirs = ensure_directories(state_dir)

    # ── Install OpenViking plugin (before config, so ov-install doesn't overwrite) ──
    if args.openviking:
        install_plugin(profile=args.profile, plugin_dir=args.plugin_dir)

    # ── Write config (always last, ensures our settings take precedence) ──
    write_config(config, config_path)
    print(f"Config written: {config_path}")

    print_summary(config, config_path, args.profile, created_dirs)

    if args.config_only:
        print("Config-only mode. Gateway not started.")
        print(f"\nTo start manually:\n  openclaw{'' if args.profile == 'default' else f' --profile {args.profile}'} gateway")
        return

    # ── Start gateway ────────────────────────────────────────────────────
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    exit_code = start_gateway(
        profile=args.profile,
        port=args.port,
        verbose=args.verbose,
        cache_trace=args.cache_trace,
        trace_dir=trace_dir,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
