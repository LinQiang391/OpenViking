"""
OpenClaw Context Capture startup script.

Starts the capture Web UI (and optionally the gateway) for a specified
OpenClaw profile, with all cache-trace and context-engine diagnostics
properly wired.

Each profile's trace data is stored in the gateway's own state directory
(~/.openclaw[-<profile>]/logs/trace/) for natural data isolation.

Prerequisites:
  - Python 3.10+ with packages: fastapi, uvicorn, pydantic
  - openclaw installed (only if using --start-gateway)
  - mitmproxy installed (only if using --proxy)

Quick start:
  # Capture for default profile (gateway already running on 18789)
  python start_capture.py --profile default --gateway-port 18789

  # Capture for second profile on a different Web UI port
  python start_capture.py --profile second --gateway-port 18890 --ui-port 9002

  # Start gateway + capture together
  python start_capture.py --profile default --gateway-port 18789 --start-gateway

  # With mitmproxy (full HTTP capture)
  python start_capture.py --profile default --gateway-port 18789 --proxy --proxy-port 18080
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional


_SELF_DIR = Path(__file__).resolve().parent
_VENDOR_CAPTURE = _SELF_DIR / "vendor" / "ai_toolbox" / "openclaw_capture_context_tool"
TOOLKIT_ROOT = _VENDOR_CAPTURE if _VENDOR_CAPTURE.is_dir() else _SELF_DIR
CAPTURE_TOOL_DIR = TOOLKIT_ROOT / "capture_tool"


def resolve_state_dir(profile: str) -> Path:
    home = Path.home()
    if profile == "default":
        return home / ".openclaw"
    return home / f".openclaw-{profile}"


def resolve_data_dir(profile: str) -> Path:
    """Default trace data lives in the gateway's own profile directory."""
    state_dir = resolve_state_dir(profile)
    return state_dir / "logs" / "trace"


def find_gateway_log(profile: str) -> Optional[Path]:
    """Find the gateway log file for the given profile."""
    state_dir = resolve_state_dir(profile)
    candidates = [
        state_dir / "logs" / f"openclaw-profile-{profile}.log",
        state_dir / "logs" / f"openclaw-{profile}.log",
        state_dir / "logs" / "openclaw.log",
    ]
    for c in candidates:
        if c.exists():
            return c

    if profile == "default":
        temp = Path(os.environ.get("TEMP", "/tmp"))
        for p in temp.glob("openclaw/openclaw-*.log"):
            return p

    log_dir = state_dir / "logs"
    if log_dir.exists():
        logs = sorted(log_dir.glob("openclaw*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            return logs[0]
    return None


def setup_cache_trace_env(data_dir: Path) -> dict[str, str]:
    """Build environment variables for OpenClaw cache-trace."""
    data_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "OPENCLAW_CACHE_TRACE": "1",
        "OPENCLAW_CACHE_TRACE_FILE": str(data_dir / "cache-trace.jsonl"),
        "OPENCLAW_CACHE_TRACE_MESSAGES": "1",
        "OPENCLAW_CACHE_TRACE_PROMPT": "1",
        "OPENCLAW_CACHE_TRACE_SYSTEM": "1",
        "OPENVIKING_DIAGNOSTICS_PATH": str(data_dir / "openviking-diagnostics.jsonl"),
    }
    return env


def start_gateway_process(
    profile: str,
    port: int,
    data_dir: Path,
    verbose: bool = True,
) -> Optional[subprocess.Popen]:
    """Start an OpenClaw gateway with cache-trace enabled."""
    openclaw_exe = shutil.which("openclaw")
    if not openclaw_exe:
        print("ERROR: 'openclaw' not found on PATH.", file=sys.stderr)
        return None

    env = os.environ.copy()
    env.pop("OPENCLAW_HOME", None)
    env.pop("OPENCLAW_STATE_DIR", None)
    env.update(setup_cache_trace_env(data_dir))

    cmd = [openclaw_exe]
    if profile != "default":
        cmd.extend(["--profile", profile])
    cmd.extend(["gateway", "--port", str(port), "--bind", "loopback"])
    if verbose:
        cmd.append("--verbose")

    print(f"  Starting gateway: {' '.join(cmd)}")
    gateway_log = data_dir / "gateway-stdout.log"
    log_fh = open(gateway_log, "w", encoding="utf-8")

    proc = subprocess.Popen(
        cmd, env=env,
        stdout=log_fh, stderr=subprocess.STDOUT,
    )
    print(f"  Gateway PID: {proc.pid}, log: {gateway_log}")
    return proc


def start_proxy_process(
    proxy_host: str,
    proxy_port: int,
    data_dir: Path,
) -> Optional[subprocess.Popen]:
    """Start mitmproxy to capture HTTP/WS traffic."""
    mitmdump = shutil.which("mitmdump")
    if not mitmdump:
        print("ERROR: 'mitmdump' not found on PATH. Install mitmproxy first.", file=sys.stderr)
        return None

    addon = CAPTURE_TOOL_DIR / "tools" / "context_capture" / "proxy_addon.py"
    if not addon.exists():
        print(f"ERROR: proxy addon not found: {addon}", file=sys.stderr)
        return None

    data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        mitmdump,
        "-s", str(addon),
        "--listen-host", proxy_host,
        "--listen-port", str(proxy_port),
        "--set", f"context_capture_data_dir={data_dir}",
    ]

    print(f"  Starting mitmproxy: {proxy_host}:{proxy_port}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(f"  mitmproxy PID: {proc.pid}")
    return proc


def start_web_ui(
    data_dir: Path,
    ui_host: str,
    ui_port: int,
    gateway_log_path: Optional[Path],
) -> None:
    """Start the capture Web UI (blocking)."""
    sys.path.insert(0, str(CAPTURE_TOOL_DIR))

    os.environ["CONTEXT_CAPTURE_DATA_DIR"] = str(data_dir)
    if gateway_log_path and gateway_log_path.exists():
        os.environ["CONTEXT_CAPTURE_GATEWAY_LOG_PATH"] = str(gateway_log_path)

    try:
        import uvicorn
        from tools.context_capture.api import create_app
    except ImportError as e:
        print(f"ERROR: Missing dependency: {e}", file=sys.stderr)
        print(f"  Run: pip install -r {TOOLKIT_ROOT / 'requirements.txt'}")
        sys.exit(1)

    app = create_app(data_dir=data_dir)
    print(f"\n  Capture Web UI: http://{ui_host}:{ui_port}/")
    print(f"  Data directory: {data_dir}")
    if gateway_log_path:
        print(f"  Gateway log: {gateway_log_path}")
    print()

    uvicorn.run(app, host=ui_host, port=ui_port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(
        description="Start OpenClaw context capture for a specific gateway profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Web UI only (gateway already running)
  python start_capture.py --profile default --gateway-port 18789

  # Different profile, different UI port
  python start_capture.py --profile second --gateway-port 18890 --ui-port 9002

  # Start gateway + capture together
  python start_capture.py --profile default --gateway-port 18789 --start-gateway

  # Full capture with mitmproxy
  python start_capture.py --profile default --gateway-port 18789 --proxy --proxy-port 18080
""",
    )

    parser.add_argument("--profile", required=True, help="OpenClaw profile name (default/second/custom)")
    parser.add_argument("--gateway-port", type=int, required=True, help="Gateway port to capture")
    parser.add_argument("--ui-host", default="127.0.0.1", help="Web UI listen host (default: 127.0.0.1)")
    parser.add_argument("--ui-port", type=int, default=9001, help="Web UI listen port (default: 9001)")
    parser.add_argument("--start-gateway", action="store_true", help="Also start the gateway (with cache-trace)")
    parser.add_argument("--proxy", action="store_true", help="Start mitmproxy for HTTP capture")
    parser.add_argument("--proxy-host", default="127.0.0.1", help="mitmproxy listen host")
    parser.add_argument("--proxy-port", type=int, default=18080, help="mitmproxy listen port")
    parser.add_argument("--data-dir", default=None, help="Override data directory")
    parser.add_argument("--gateway-log", default=None, help="Override gateway log file path")

    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else resolve_data_dir(args.profile)
    data_dir.mkdir(parents=True, exist_ok=True)

    child_procs: list[subprocess.Popen] = []

    def cleanup(signum=None, frame=None):
        print("\nShutting down...")
        for proc in child_procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, cleanup)

    print("=" * 60)
    print(f"  OpenClaw Context Capture")
    print("=" * 60)
    print(f"  Profile       : {args.profile}")
    print(f"  Gateway port  : {args.gateway_port}")
    print(f"  Data dir      : {data_dir}")
    print(f"  Web UI        : http://{args.ui_host}:{args.ui_port}/")

    cache_trace_env = setup_cache_trace_env(data_dir)
    for k, v in cache_trace_env.items():
        os.environ[k] = v

    if args.start_gateway:
        gw_proc = start_gateway_process(
            profile=args.profile,
            port=args.gateway_port,
            data_dir=data_dir,
        )
        if gw_proc:
            child_procs.append(gw_proc)
            time.sleep(3)
        else:
            print("WARNING: Gateway failed to start, continuing with UI only.")

    if args.proxy:
        proxy_proc = start_proxy_process(
            proxy_host=args.proxy_host,
            proxy_port=args.proxy_port,
            data_dir=data_dir,
        )
        if proxy_proc:
            child_procs.append(proxy_proc)
            print(f"  Proxy         : http://{args.proxy_host}:{args.proxy_port}/")

    gateway_log = Path(args.gateway_log) if args.gateway_log else find_gateway_log(args.profile)
    if gateway_log:
        print(f"  Gateway log   : {gateway_log}")
    else:
        print(f"  Gateway log   : not found (context-engine logs may be limited)")

    print("=" * 60)
    print()

    start_web_ui(
        data_dir=data_dir,
        ui_host=args.ui_host,
        ui_port=args.ui_port,
        gateway_log_path=gateway_log,
    )


if __name__ == "__main__":
    main()
