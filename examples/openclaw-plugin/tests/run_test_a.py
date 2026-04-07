"""
A-group full test runner for LocoMo evaluation.

Reads a config JSON (default: config-A.json), then executes the complete flow:
  0  Cleanup old environment
  1  Ensure OpenViking server is running
  2  Create OpenViking tenant (obtain user_key)
  3  Deploy gateway config (plugin install + openclaw.json)
  4  Start gateway (background, with cache-trace)
  5  Start capture tool (Web UI)
  6  Verify API connectivity
  7  Ingest sample conversations
  8  QA evaluation
  9  Summary

Usage:
  python run_test_a.py                    # uses config-A.json
  python run_test_a.py config-A.json      # explicit config path
  python run_test_a.py --skip-cleanup     # keep existing env, skip step 0
  python run_test_a.py --skip-ingest      # skip ingest (reuse existing data)
  python run_test_a.py --skip-qa          # skip QA

All companion scripts live in this directory:
  deploy_gateway.py     - Generate openclaw.json + install plugin
  cleanup_gateway.py    - Delete gateway profile directories
  cleanup_ov_data.py    - Delete OpenViking data directory
  (eval.py lives in ../../openclaw-eval/)
  (start_capture.py lives in ../../ai_toolbox/openclaw_capture_context_tool/)
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
VENDOR_DIR = SCRIPT_DIR / "vendor"

def _resolve_eval_dir() -> Path:
    vendor_path = VENDOR_DIR / "openclaw-eval"
    if (vendor_path / "eval.py").is_file():
        return vendor_path
    fallback = REPO_ROOT / "openclaw-eval"
    if (fallback / "eval.py").is_file():
        return fallback
    raise FileNotFoundError(
        f"eval.py not found in vendor ({vendor_path}) or repo root ({fallback}).\n"
        "Run: git submodule update --init --recursive"
    )

def _resolve_capture_dir() -> Path:
    vendor_path = VENDOR_DIR / "ai_toolbox" / "openclaw_capture_context_tool"
    if (vendor_path / "start_capture.py").is_file():
        return vendor_path
    fallback = REPO_ROOT / "ai_toolbox" / "openclaw_capture_context_tool"
    if (fallback / "start_capture.py").is_file():
        return fallback
    return vendor_path  # allow graceful degradation; step5 checks CAPTURE_SCRIPT.exists()

EVAL_DIR = _resolve_eval_dir()
CAPTURE_DIR = _resolve_capture_dir()

DEPLOY_SCRIPT = SCRIPT_DIR / "deploy_gateway.py"
CLEANUP_GW_SCRIPT = SCRIPT_DIR / "cleanup_gateway.py"
CLEANUP_OV_SCRIPT = SCRIPT_DIR / "cleanup_ov_data.py"
EVAL_SCRIPT = EVAL_DIR / "eval.py"
CAPTURE_SCRIPT = SCRIPT_DIR / "start_capture.py"


def _utf8_env(base: dict | None = None) -> dict:
    """Return an env dict with PYTHONUTF8=1 to avoid GBK issues on Windows."""
    env = (base or os.environ).copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _log(step: int, msg: str) -> None:
    print(f"  [{step}] {msg}")


def _banner(step: int, title: str) -> None:
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Step {step}: {title}")
    print(sep)


def _is_port_open(host: str, port: int, timeout: float = 2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _wait_for_port(host: str, port: int, timeout: float = 30, interval: float = 1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_open(host, port):
            return True
        time.sleep(interval)
    return False


def _api_post(url: str, body: dict, headers: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _find_pid_on_port(port: int) -> int | None:
    """Find the PID listening on *port*, or None."""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True, encoding="utf-8", errors="replace",
        )
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                return int(line.strip().split()[-1])
    except Exception:
        pass
    return None


def _kill_pid_on_port(port: int, label: str = "") -> bool:
    """Find and kill the process listening on *port*. Returns True if killed."""
    pid = _find_pid_on_port(port)
    if pid is None:
        return False
    tag = f" ({label})" if label else ""
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        print(f"    ✓ 端口 {port}{tag}: 已停止 PID {pid}")
        return True
    except Exception:
        print(f"    ✗ 端口 {port}{tag}: 无法停止 PID {pid}")
        return False


def _resolve_state_dir(profile: str) -> Path:
    if profile == "default":
        return Path.home() / ".openclaw"
    return Path.home() / f".openclaw-{profile}"


# ── Steps ────────────────────────────────────────────────────────────────────

def step0_cleanup(cfg: dict) -> None:
    _banner(0, "清理环境")

    profile = cfg["profile"]
    gw_port = cfg["gateway_port"]
    capture_port = cfg.get("capture_port")
    needs_ov = cfg.get("deploy", {}).get("openviking", True)
    ov_port = int(cfg["openviking"]["url"].rsplit(":", 1)[-1]) if needs_ov and cfg.get("openviking") else None

    # ── 1. 停止占用端口的进程 ──
    print("  检查端口占用...")
    killed_any = False
    killed_any |= _kill_pid_on_port(gw_port, "Gateway")
    if capture_port:
        killed_any |= _kill_pid_on_port(capture_port, "Capture UI")
    if needs_ov and ov_port:
        killed_any |= _kill_pid_on_port(ov_port, "OpenViking Server")
    if not killed_any:
        print("    无占用端口")
    if killed_any:
        time.sleep(2)

    # ── 2. 清理 gateway profile 目录 ──
    print("  清理 Gateway Profile...")
    subprocess.run(
        [sys.executable, str(CLEANUP_GW_SCRIPT), profile, "--force"],
        cwd=str(REPO_ROOT), env=_utf8_env(),
    )

    # ── 3. 清理 OpenViking 数据（仅 OV 模式）──
    if needs_ov:
        print("  清理 OpenViking 数据...")
        result = subprocess.run(
            [sys.executable, str(CLEANUP_OV_SCRIPT), "--force", "--stop", "--keep-log"],
            cwd=str(REPO_ROOT), env=_utf8_env(),
        )
        ov_data_dir = Path.home() / ".openviking" / "data"
        if result.returncode != 0 and ov_data_dir.exists():
            _log(0, "cleanup_ov_data.py 失败，尝试强制删除...")
            for subdir in ["_system", "vectordb", "viking"]:
                p = ov_data_dir / subdir
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
            _log(0, "✓ 关键数据目录已强制删除")

    # ── 4. 清理评测输出 ──
    output_dir = EVAL_DIR / cfg["test"]["output_dir"]
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
        print(f"  ✓ 清理输出目录: {output_dir}")

    # ── 5. 清理 ingest 追踪记录（仅 OV 模式，避免影响其他组结果）──
    if needs_ov:
        ingest_record = EVAL_DIR / ".ingest_record.json"
        if ingest_record.exists():
            ingest_record.unlink()
            print(f"  ✓ 清理 ingest 记录: {ingest_record.name}")

    # ── 6. 确认端口已释放 ──
    print("  确认端口释放...")
    all_clear = True
    ports_to_check = [(gw_port, "Gateway")]
    if needs_ov and ov_port:
        ports_to_check.append((ov_port, "OV Server"))
    if capture_port:
        ports_to_check.append((capture_port, "Capture"))
    for port, label in ports_to_check:
        if _is_port_open("127.0.0.1", port):
            print(f"    ✗ 端口 {port} ({label}) 仍被占用!")
            all_clear = False
        else:
            print(f"    ✓ 端口 {port} ({label}) 已释放")

    if all_clear:
        print("\n  ✓ 环境清理完成")
    else:
        print("\n  ⚠ 部分端口未释放，可能影响后续步骤")


def step1_ensure_ov_server(cfg: dict) -> subprocess.Popen | None:
    _banner(1, "确保 OpenViking 服务器运行")

    ov = cfg["openviking"]
    host = "127.0.0.1"
    port = int(ov["url"].rsplit(":", 1)[-1])

    if _is_port_open(host, port):
        _log(1, f"OpenViking 已运行 ({host}:{port})")
        return None

    ov_conf = ov.get("ov_conf")
    if not ov_conf:
        ov_conf = str(Path.home() / ".openviking" / "ov.conf")

    _log(1, f"启动 OpenViking (config={ov_conf}) ...")
    log_dir = EVAL_DIR / cfg["test"]["output_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ov_server.log"

    fh = open(log_file, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "openviking.server.bootstrap", "--config", ov_conf],
        stdout=fh, stderr=subprocess.STDOUT,
        env=_utf8_env(),
    )

    if not _wait_for_port(host, port, timeout=30):
        _log(1, "ERROR: OpenViking 启动超时")
        fh.close()
        proc.terminate()
        sys.exit(1)

    _log(1, f"✓ OpenViking 启动成功 (PID {proc.pid})")
    _log(1, f"  server log → {log_file}")
    return proc


def _api_delete(url: str, headers: dict, timeout: float = 10) -> dict | None:
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return None


def step2_create_tenant(cfg: dict) -> str:
    _banner(2, "创建 OpenViking 租户")

    ov = cfg["openviking"]
    base = ov["url"]
    account_id = ov["account_id"]
    user_id = ov["user_id"]
    admin_headers = {"Content-Type": "application/json", "X-API-Key": ov["root_api_key"]}

    # Delete existing account first (best-effort, ignore 404)
    _log(2, f"清理旧账户 {account_id} (如存在)...")
    _api_delete(f"{base}/api/v1/admin/accounts/{account_id}", admin_headers)

    try:
        result = _api_post(
            f"{base}/api/v1/admin/accounts",
            {"account_id": account_id, "admin_user_id": user_id},
            admin_headers,
            timeout=10,
        )
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        _log(2, f"ERROR: HTTP {exc.code} — {err_body}")
        sys.exit(1)

    user_key = result["result"]["user_key"]
    _log(2, f"account  = {account_id}")
    _log(2, f"user     = {user_id}")
    _log(2, f"user_key = {user_key}")
    return user_key


def step3_deploy_config(cfg: dict, ov_user_key: str | None) -> str:
    _banner(3, "部署 Gateway 配置")

    deploy = cfg.get("deploy", {})
    enable_ov = deploy.get("openviking", True)
    enable_mc = deploy.get("mem_core", False)

    cmd = [
        sys.executable, str(DEPLOY_SCRIPT),
        "--profile", cfg["profile"],
        "--port", str(cfg["gateway_port"]),
        "--openviking" if enable_ov else "--no-openviking",
        "--mem-core" if enable_mc else "--no-mem-core",
        "--volcengine-key", cfg["volcengine_key"],
        "--cache-trace",
        "--config-only",
    ]

    if enable_ov:
        ov = cfg["openviking"]
        cmd.extend([
            "--ov-api-key", ov_user_key or "",
            "--ov-agent-id", ov.get("agent_id", cfg["profile"]),
            "--ov-url", ov["url"],
        ])

    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=_utf8_env())
    if proc.returncode != 0:
        _log(3, f"ERROR: deploy_gateway.py 失败 (exit {proc.returncode})")
        sys.exit(1)

    config_path = _resolve_state_dir(cfg["profile"]) / "openclaw.json"
    with open(config_path, encoding="utf-8") as f:
        gw_cfg = json.load(f)
    token = gw_cfg["gateway"]["auth"]["token"]
    _log(3, f"Token = {token}")
    return token


def step4_start_gateway(cfg: dict) -> subprocess.Popen:
    _banner(4, "启动 Gateway")

    openclaw_exe = shutil.which("openclaw")
    if not openclaw_exe:
        _log(4, "ERROR: openclaw 不在 PATH 中")
        sys.exit(1)

    profile = cfg["profile"]
    port = cfg["gateway_port"]
    state_dir = _resolve_state_dir(profile)
    trace_dir = state_dir / "logs" / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)

    env = _utf8_env()
    env.pop("OPENCLAW_HOME", None)
    env.pop("OPENCLAW_STATE_DIR", None)

    if cfg.get("cache_trace", True):
        env["OPENCLAW_CACHE_TRACE"] = "1"
        env["OPENCLAW_CACHE_TRACE_FILE"] = str(trace_dir / "cache-trace.jsonl")
        env["OPENCLAW_CACHE_TRACE_MESSAGES"] = "1"
        env["OPENCLAW_CACHE_TRACE_PROMPT"] = "1"
        env["OPENCLAW_CACHE_TRACE_SYSTEM"] = "1"
        env["OPENVIKING_DIAGNOSTICS_PATH"] = str(trace_dir / "openviking-diagnostics.jsonl")

    cmd = [openclaw_exe]
    if profile != "default":
        cmd.extend(["--profile", profile])
    cmd.extend(["gateway", "--port", str(port), "--bind", "loopback"])
    if cfg.get("verbose", True):
        cmd.append("--verbose")

    log_dir = EVAL_DIR / cfg["test"]["output_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    gw_log = log_dir / "gateway.log"
    gw_jsonl = trace_dir / "gateway.log.jsonl"

    proc = subprocess.Popen(
        cmd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    from deploy_gateway import _tee_to_jsonl  # reuse JSONL capture logic
    tee_thread = threading.Thread(
        target=_tee_to_jsonl, args=(proc, gw_jsonl),
        kwargs={"quiet": True}, daemon=True,
    )
    tee_thread.start()

    _log(4, f"Gateway PID: {proc.pid}")
    _log(4, f"等待端口 {port} 就绪...")

    if not _wait_for_port("127.0.0.1", port, timeout=60):
        _log(4, "ERROR: Gateway 启动超时 (60s)")
        proc.terminate()
        tee_thread.join(timeout=2)
        sys.exit(1)

    _log(4, f"✓ Gateway 启动成功: http://127.0.0.1:{port}")
    _log(4, f"  cache-trace   → {trace_dir}")
    _log(4, f"  gateway jsonl → {gw_jsonl}")
    return proc


def step5_start_capture(cfg: dict) -> subprocess.Popen | None:
    _banner(5, "启动抓包工具")

    capture_port = cfg.get("capture_port")
    if not capture_port:
        _log(5, "未配置 capture_port，跳过")
        return None

    if not CAPTURE_SCRIPT.exists():
        _log(5, f"WARNING: 抓包脚本不存在: {CAPTURE_SCRIPT}")
        return None

    profile = cfg["profile"]
    gw_port = cfg["gateway_port"]

    cmd = [
        sys.executable, str(CAPTURE_SCRIPT),
        "--profile", profile,
        "--gateway-port", str(gw_port),
        "--ui-port", str(capture_port),
    ]

    log_dir = EVAL_DIR / cfg["test"]["output_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    cap_log = log_dir / "capture.log"
    fh = open(cap_log, "w", encoding="utf-8")

    proc = subprocess.Popen(
        cmd, stdout=fh, stderr=subprocess.STDOUT,
        cwd=str(CAPTURE_DIR), env=_utf8_env(),
    )

    _log(5, f"Capture PID: {proc.pid} (port={capture_port})")
    if not _wait_for_port("127.0.0.1", capture_port, timeout=15):
        _log(5, "WARNING: 抓包工具启动超时（非致命，继续）")
    else:
        _log(5, f"✓ 抓包工具启动成功: http://127.0.0.1:{capture_port}/")
        _log(5, f"  capture log → {cap_log}")

    return proc


def step6_verify(cfg: dict, token: str) -> None:
    _banner(6, "验证 API 连通性")

    port = cfg["gateway_port"]
    url = f"http://127.0.0.1:{port}/v1/responses"
    body = {"model": "openclaw", "input": "hello", "stream": False, "user": "api-test"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        result = _api_post(url, body, headers, timeout=60)
    except Exception as exc:
        _log(6, f"ERROR: {exc}")
        sys.exit(1)

    status = result.get("status")
    output = result.get("output", [])
    text = output[0]["content"][0]["text"][:120] if output else "(empty)"
    usage = result.get("usage", {})
    _log(6, f"Status   : {status}")
    _log(6, f"Response : {text}...")
    _log(6, f"Tokens   : in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}")


def step7_ingest(cfg: dict, token: str) -> None:
    _banner(7, "Ingest")

    test = cfg["test"]
    output_dir = EVAL_DIR / test["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / test["ingest_output"]
    data_file = EVAL_DIR / test["data_file"]

    cmd = [
        sys.executable, str(EVAL_SCRIPT), "ingest", str(data_file),
        "--sample", str(test["sample"]),
        "--user", test.get("user_id") or (cfg.get("openviking") or {}).get("user_id", "eval-1"),
        "--output", str(output_file),
        "--token", token,
        "--base-url", f"http://127.0.0.1:{cfg['gateway_port']}",
        "--agent-id", test.get("agent_id") or (cfg.get("openviking") or {}).get("account_id", "locomo-eval"),
        "--tail", test.get("ingest_tail", "[]"),
    ]

    _log(7, f"Data     : {data_file.name}")
    _log(7, f"Sample   : {test['sample']}")
    _log(7, f"Output   : {output_file}")

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(EVAL_DIR), env=_utf8_env())
    elapsed = time.time() - t0

    if proc.returncode != 0:
        _log(7, f"ERROR: Ingest 失败 (exit {proc.returncode})")
        sys.exit(1)

    _log(7, f"✓ Ingest 完成 ({elapsed:.0f}s)")


def step8_qa(cfg: dict, token: str) -> None:
    _banner(8, "QA")

    test = cfg["test"]
    output_dir = EVAL_DIR / test["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / test["qa_output"]
    data_file = EVAL_DIR / test["data_file"]

    cmd = [
        sys.executable, str(EVAL_SCRIPT), "qa", str(data_file),
        "--sample", str(test["sample"]),
        "--user", test.get("user_id") or (cfg.get("openviking") or {}).get("user_id", "eval-1"),
        "--output", str(output_file),
        "--token", token,
        "--base-url", f"http://127.0.0.1:{cfg['gateway_port']}",
        "--agent-id", test.get("agent_id") or (cfg.get("openviking") or {}).get("account_id", "locomo-eval"),
    ]

    qa_count = test.get("qa_count")
    if qa_count:
        cmd.extend(["--count", str(qa_count)])

    _log(8, f"Output   : {output_file}")
    if qa_count:
        _log(8, f"Count    : {qa_count} (共 199)")

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(EVAL_DIR), env=_utf8_env())
    elapsed = time.time() - t0

    if proc.returncode != 0:
        _log(8, f"ERROR: QA 失败 (exit {proc.returncode})")
        sys.exit(1)

    _log(8, f"✓ QA 完成 ({elapsed:.0f}s)")


def step9_summary(cfg: dict, token: str, ov_user_key: str) -> None:
    _banner(9, "测试完成")

    output_dir = EVAL_DIR / cfg["test"]["output_dir"]
    capture_port = cfg.get("capture_port")
    deploy = cfg.get("deploy", {})
    mode = "OpenViking" if deploy.get("openviking") else "memory-core"
    print(f"  Mode        : {mode}")
    print(f"  Profile     : {cfg['profile']}")
    print(f"  Gateway     : http://127.0.0.1:{cfg['gateway_port']}")
    print(f"  Token       : {token}")
    if ov_user_key:
        print(f"  OV user_key : {ov_user_key}")
    if capture_port:
        print(f"  Capture UI  : http://127.0.0.1:{capture_port}/")
    print(f"  Output dir  : {output_dir.resolve()}")

    if output_dir.exists():
        print()
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                size = f.stat().st_size
                print(f"    {f.name:40s} {size:>10,} bytes")

    pid_file = output_dir / ".pids.json"
    if pid_file.exists():
        with open(pid_file, encoding="utf-8") as f:
            pids = json.load(f)
        print(f"\n  后台进程 (PID 保存在 {pid_file.name}):")
        for name, pid in pids.items():
            print(f"    {name:15s} PID {pid}")
        print(f"\n  停止命令:")
        pid_list = " ".join(f"/PID {p}" for p in pids.values())
        print(f"    taskkill /F {pid_list}")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="A-group full test runner")
    parser.add_argument("config", nargs="?", default="config-A.json",
                        help="Path to config JSON (default: config-A.json)")
    parser.add_argument("--skip-cleanup", action="store_true",
                        help="Skip step 0 (cleanup)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Skip step 7 (ingest)")
    parser.add_argument("--skip-qa", action="store_true",
                        help="Skip step 8 (QA)")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = SCRIPT_DIR / config_path
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    bg_pids: dict[str, int] = {}

    try:
        if cfg.get("cleanup_before_run", True) and not args.skip_cleanup:
            step0_cleanup(cfg)

        output_dir = EVAL_DIR / cfg["test"]["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)

        needs_ov = cfg.get("deploy", {}).get("openviking", True)
        ov_user_key = None

        if needs_ov:
            ov_proc = step1_ensure_ov_server(cfg)
            if ov_proc:
                bg_pids["ov_server"] = ov_proc.pid
            ov_user_key = step2_create_tenant(cfg)
        else:
            _banner(1, "跳过 (无 OpenViking 依赖)")
            _banner(2, "跳过 (无 OpenViking 依赖)")

        token = step3_deploy_config(cfg, ov_user_key)

        _log(3, "等待 ov-install 完成 (5s)...")
        time.sleep(5)

        gw_proc = step4_start_gateway(cfg)
        bg_pids["gateway"] = gw_proc.pid

        cap_proc = step5_start_capture(cfg)
        if cap_proc:
            bg_pids["capture"] = cap_proc.pid

        pid_file = output_dir / ".pids.json"
        with open(pid_file, "w", encoding="utf-8") as f:
            json.dump(bg_pids, f, indent=2)

        step6_verify(cfg, token)

        if not args.skip_ingest:
            step7_ingest(cfg, token)

        if not args.skip_qa:
            step8_qa(cfg, token)

        step9_summary(cfg, token, ov_user_key)

    except KeyboardInterrupt:
        print("\n  中断")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
