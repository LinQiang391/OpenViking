"""
LoCoMo Benchmark - OpenClaw MemCore unified test runner.

Orchestrates the complete pipeline:
  clean → generate openclaw.json → ingest → QA → judge → stat → archive

Usage:
    python run_benchmark.py                         # uses config.toml
    python run_benchmark.py --config config.local.toml
    python run_benchmark.py --config config.local.toml --only ingest,qa
    python run_benchmark.py --config config.local.toml --skip judge,archive
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        print("ERROR: Python 3.11+ required (for tomllib), or install tomli: pip install tomli")
        sys.exit(1)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALL_STEPS = ["stop_gateway", "stop_openviking", "clean", "start_gateway", "start_openviking", "ingest", "snapshot_ingest", "qa", "judge", "stat", "archive"]
INGEST_STAGING_DIR = os.path.join(SCRIPT_DIR, ".ingest_sessions_staging")
_gateway_proc = None
_openviking_proc = None


def load_config(config_path: str) -> dict:
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def expand_path(p: str) -> str:
    return os.path.expanduser(os.path.expandvars(p))


def run_cmd(cmd: list[str], description: str, cwd: str | None = None) -> int:
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=cwd or SCRIPT_DIR, shell=True)
    if result.returncode != 0:
        print(f"[WARN] Command exited with code {result.returncode}")
    return result.returncode


def generate_openclaw_json(cfg: dict) -> str:
    """Generate openclaw.json from config and write to openclaw_dir. Returns the path."""
    openclaw_dir = expand_path(cfg["general"]["openclaw_dir"])
    vlm = cfg["vlm"]
    emb = cfg.get("embedding", {})
    mem = cfg.get("memory_search", {})
    gw = cfg["gateway"]

    comp = cfg.get("compaction", {})
    memory_mode = cfg.get("memory", {}).get("mode", "none")

    oc = {
        "agents": {
            "defaults": {
                "models": {f"{vlm['provider']}/{vlm['model_id']}": {}},
                "model": {"primary": f"{vlm['provider']}/{vlm['model_id']}"},
                "thinkingDefault": "adaptive",
            }
        },
        "gateway": {
            "mode": "local",
            "auth": {"mode": "token", "token": gw["token"]},
            "port": gw["port"],
            "bind": "loopback",
            "tailscale": {"mode": "off"},
            "controlUi": {"allowInsecureAuth": True},
            "http": {"endpoints": {"responses": {"enabled": True}}},
        },
        "models": {
            "providers": {
                vlm["provider"]: {
                    "baseUrl": vlm["base_url"],
                    "apiKey": vlm["api_key"],
                    "api": vlm.get("api", "anthropic-messages"),
                    "models": [
                        {
                            "id": vlm["model_id"],
                            "name": vlm["model_id"],
                            "reasoning": True,
                            "input": ["text"],
                            "contextWindow": 256000,
                            "maxTokens": 4096,
                        }
                    ],
                }
            }
        },
        "session": {"dmScope": "per-channel-peer"},
        "tools": {"profile": "coding"},
        "auth": {
            "profiles": {"volcengine:default": {"provider": "volcengine", "mode": "api_key"}},
            "order": {"volcengine": ["volcengine:default"]},
        },
        "plugins": {"entries": {"volcengine": {"enabled": True}}},
    }

    if memory_mode in ("memcore", "both") and emb.get("enabled", True):
        query_cfg = {}
        if mem.get("hybrid_enabled", True):
            query_cfg["hybrid"] = {
                "enabled": True,
                "vectorWeight": mem.get("vector_weight", 0.7),
                "textWeight": mem.get("text_weight", 0.3),
            }
        query_cfg["minScore"] = mem.get("min_score", 0)
        if mem.get("max_results"):
            query_cfg["maxResults"] = mem["max_results"]

        oc["agents"]["defaults"]["memorySearch"] = {
            "provider": emb.get("provider", "openai"),
            "model": emb["model"],
            "remote": {
                "baseUrl": emb["base_url"],
                "apiKey": emb.get("api_key", vlm["api_key"]),
            },
            "query": query_cfg,
        }

    if memory_mode in ("memcore", "both") and comp.get("memory_flush_enabled", False):
        compaction_cfg = {"memoryFlush": {"enabled": True}}
        reserve = comp.get("reserve_tokens_floor")
        if reserve is not None:
            compaction_cfg["reserveTokensFloor"] = reserve
        oc["agents"]["defaults"]["compaction"] = compaction_cfg

    if memory_mode in ("openviking", "both"):
        ov = cfg.get("openviking", {})
        ov_config = {}
        passthrough_keys = [
            "mode", "baseUrl", "apiKey", "autoCapture", "autoRecall",
            "commitTokenThreshold", "ingestReplyAssist", "recallLimit",
            "recallTokenBudget", "recallScoreThreshold", "recallMaxContentChars",
            "recallPreferAbstract", "captureMode", "captureMaxLength",
            "bypassSessionPatterns", "targetUri", "timeoutMs", "agentId",
            "emitStandardDiagnostics", "logFindRequests",
        ]
        for key in passthrough_keys:
            if key in ov:
                ov_config[key] = ov[key]
        oc["plugins"]["entries"]["openviking"] = {
            "enabled": True,
            "config": ov_config,
        }
        oc["plugins"]["slots"] = {"contextEngine": "openviking"}
        plugin_path = ov.get("pluginPath")
        if plugin_path:
            oc["plugins"].setdefault("load", {}).setdefault("paths", []).append(
                expand_path(plugin_path)
            )

    out_path = os.path.join(openclaw_dir, "openclaw.json")
    os.makedirs(openclaw_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(oc, f, indent=2, ensure_ascii=False)
    print(f"[OK] Generated {out_path}")
    return out_path


def _find_gateway_pid(port: int) -> int | None:
    """Find PID of the process listening on the gateway port."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, shell=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    return int(parts[-1])
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().splitlines()[0])
    except Exception as e:
        print(f"  [gateway] Could not find PID on port {port}: {e}")
    return None


def step_stop_gateway(cfg: dict):
    """Stop any running openclaw gateway process."""
    global _gateway_proc
    port = cfg["gateway"]["port"]
    pid = _find_gateway_pid(port)

    if pid:
        print(f"  Stopping gateway (PID {pid}) on port {port}...")
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], shell=True, capture_output=True)
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            print(f"  Gateway stopped.")
        except Exception as e:
            print(f"  [WARN] Could not stop gateway: {e}")
    else:
        print(f"  No gateway found on port {port}.")

    if _gateway_proc and _gateway_proc.poll() is None:
        _gateway_proc.terminate()
        _gateway_proc.wait(timeout=5)
        _gateway_proc = None


def step_start_gateway(cfg: dict):
    """Start openclaw gateway in the background and wait for it to be ready."""
    global _gateway_proc
    port = cfg["gateway"]["port"]

    generate_openclaw_json(cfg)

    print(f"  Starting openclaw gateway on port {port} (new console window)...")
    if sys.platform == "win32":
        _gateway_proc = subprocess.Popen(
            "openclaw gateway",
            shell=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        log_path = os.path.join(SCRIPT_DIR, "gateway.log")
        log_file = open(log_path, "w", encoding="utf-8")
        _gateway_proc = subprocess.Popen(
            ["openclaw", "gateway"],
            stdout=log_file,
            stderr=log_file,
        )

    import urllib.request
    url = f"http://127.0.0.1:{port}/v1/responses"
    max_wait = 30
    for i in range(max_wait):
        time.sleep(1)
        try:
            req = urllib.request.Request(url, method="OPTIONS")
            urllib.request.urlopen(req, timeout=2)
            print(f"  Gateway ready after {i+1}s.")
            return
        except Exception:
            pass
        pid = _find_gateway_pid(port)
        if pid:
            print(f"  Gateway ready (PID {pid}) after {i+1}s.")
            return

    print(f"  [WARN] Gateway may not be ready after {max_wait}s, proceeding anyway.")


def step_stop_openviking(cfg: dict):
    """Stop any running OpenViking server process."""
    global _openviking_proc
    memory_mode = cfg.get("memory", {}).get("mode", "none")
    if memory_mode not in ("openviking", "both"):
        print(f"  Skipping (memory mode={memory_mode}, OpenViking not needed).")
        return

    ov = cfg.get("openviking", {})
    base_url = ov.get("baseUrl", "http://127.0.0.1:8080")

    try:
        from urllib.parse import urlparse
        port = urlparse(base_url).port or 8080
    except Exception:
        port = 8080

    pid = _find_gateway_pid(port)
    if pid:
        print(f"  Stopping OpenViking (PID {pid}) on port {port}...")
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], shell=True, capture_output=True)
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            print(f"  OpenViking stopped.")
        except Exception as e:
            print(f"  [WARN] Could not stop OpenViking: {e}")
    else:
        print(f"  No OpenViking found on port {port}.")

    if _openviking_proc and _openviking_proc.poll() is None:
        _openviking_proc.terminate()
        _openviking_proc.wait(timeout=5)
        _openviking_proc = None


def step_start_openviking(cfg: dict):
    """Start OpenViking server in the background (only for openviking/both modes with remote config)."""
    global _openviking_proc
    memory_mode = cfg.get("memory", {}).get("mode", "none")
    if memory_mode not in ("openviking", "both"):
        print(f"  Skipping (memory mode={memory_mode}, OpenViking not needed).")
        return

    ov = cfg.get("openviking", {})
    if ov.get("mode", "remote") == "local":
        print(f"  Skipping (openviking.mode=local, plugin will auto-start OV server).")
        return

    base_url = ov.get("baseUrl", "http://127.0.0.1:8080")
    print(f"  Starting OpenViking server (target: {base_url})...")

    if sys.platform == "win32":
        _openviking_proc = subprocess.Popen(
            "openviking server start",
            shell=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        log_path = os.path.join(SCRIPT_DIR, "openviking.log")
        log_file = open(log_path, "w", encoding="utf-8")
        _openviking_proc = subprocess.Popen(
            ["openviking", "server", "start"],
            stdout=log_file,
            stderr=log_file,
        )

    import urllib.request
    health_url = f"{base_url}/health"
    max_wait = 30
    for i in range(max_wait):
        time.sleep(1)
        try:
            req = urllib.request.Request(health_url)
            resp = urllib.request.urlopen(req, timeout=2)
            body = json.loads(resp.read())
            if body.get("status") == "ok":
                print(f"  OpenViking ready after {i+1}s.")
                return
        except Exception:
            pass

    print(f"  [WARN] OpenViking may not be ready after {max_wait}s, proceeding anyway.")


def step_clean(cfg: dict):
    agent_id = cfg["general"]["agent_id"]
    openclaw_dir = expand_path(cfg["general"]["openclaw_dir"])
    archive_dir = os.path.join(SCRIPT_DIR, "archive")
    port = cfg["gateway"]["port"]
    run_cmd(
        ["python", "test/clean_openclaw.py",
         "--openclaw-dir", openclaw_dir,
         "--agent-id", agent_id,
         "--archive-dir", archive_dir,
         "--gateway-port", str(port),
         "-y"],
        f"Clean environment (agent={agent_id}, archive first)"
    )

    result_dir = Path(SCRIPT_DIR) / "result"
    if result_dir.exists() and any(result_dir.iterdir()):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(archive_dir) / f"{timestamp}_pre-clean" / "result"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in result_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, backup_dir / f.name)
                try:
                    f.unlink()
                except PermissionError:
                    print(f"  [WARN] Cannot delete {f.name} (file locked). Archived copy saved.", file=sys.stderr)
        print(f"  Result files archived to {backup_dir} and cleaned.")

    ingest_record = Path(SCRIPT_DIR) / ".ingest_record.json"
    if ingest_record.exists():
        ingest_record.unlink()
        print(f"  Ingest record cleared.")


def step_ingest(cfg: dict):
    ing = cfg["ingest"]
    gw = cfg["gateway"]
    gen = cfg["general"]
    mem = cfg.get("memory", {})
    data_file = ing.get("data_file", gen.get("data_file", "../data/locomo10.json"))
    memory_mode = mem.get("mode", "none")

    cmd = [
        "python", "eval.py", "ingest", data_file,
        "--token", gw["token"],
        "--agent-id", gen["agent_id"],
        "--user", ing.get("user", "eval-1"),
        "--memory-mode", memory_mode,
    ]

    if ing.get("clear_record", False):
        cmd.append("--clear-ingest-record")

    sample = ing.get("sample", -1)
    if sample >= 0:
        cmd.extend(["--sample", str(sample)])

    sessions = ing.get("sessions")
    if sessions:
        cmd.extend(["--sessions", str(sessions)])

    run_cmd(cmd, f"Ingest conversations (memory={memory_mode})")


def step_snapshot_ingest(cfg: dict):
    """Move archived ingest session files to a staging dir, so QA sessions can be separated later."""
    openclaw_dir = expand_path(cfg["general"]["openclaw_dir"])
    agent_id = cfg["general"]["agent_id"]
    sessions_dir = Path(openclaw_dir) / "agents" / agent_id / "sessions"

    staging = Path(INGEST_STAGING_DIR)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    moved = 0
    for f in sorted(sessions_dir.glob("*.jsonl.*")):
        shutil.move(str(f), str(staging / f.name))
        moved += 1

    print(f"  Staged {moved} ingest session file(s) to {staging}")


def step_qa(cfg: dict):
    qa = cfg["qa"]
    gw = cfg["gateway"]
    gen = cfg["general"]
    data_file = qa.get("data_file", gen.get("data_file", "../data/locomo10.json"))

    cmd = [
        "python", "eval.py", "qa", data_file,
        "--token", gw["token"],
        "--agent-id", gen["agent_id"],
        "--parallel", str(qa.get("parallel", 5)),
    ]

    sample = qa.get("sample", -1)
    if sample >= 0:
        cmd.extend(["--sample", str(sample)])

    count = qa.get("count", -1)
    if count > 0:
        cmd.extend(["--count", str(count)])

    run_cmd(cmd, "QA evaluation")


def step_judge(cfg: dict):
    j = cfg["judge"]
    csv_path = os.path.join(SCRIPT_DIR, "result", "qa_results.csv")

    cmd = [
        "python", "judge.py",
        "--input", csv_path,
        "--token", j.get("api_key", cfg["vlm"]["api_key"]),
        "--base-url", j.get("base_url", "https://ark.cn-beijing.volces.com/api/coding/v3"),
        "--model", j.get("model", "doubao-seed-2-0-pro-260215"),
        "--parallel", str(j.get("parallel", 10)),
    ]
    run_cmd(cmd, "Judge scoring")


def step_stat(cfg: dict):
    csv_path = os.path.join(SCRIPT_DIR, "result", "qa_results.csv")
    cmd = ["python", "stat_judge_result.py", "--input", csv_path]
    run_cmd(cmd, "Statistics")


def step_archive(cfg: dict):
    gen = cfg["general"]
    cmd = [
        "python", "test/archive_run.py",
        "--name", gen["name"],
        "--openclaw-dir", expand_path(gen["openclaw_dir"]),
        "--agent-id", gen["agent_id"],
    ]
    if os.path.isdir(INGEST_STAGING_DIR):
        cmd.extend(["--ingest-sessions-dir", INGEST_STAGING_DIR])
    run_cmd(cmd, "Archive test data")


STEP_MAP = {
    "clean": step_clean,
    "stop_gateway": step_stop_gateway,
    "stop_openviking": step_stop_openviking,
    "start_gateway": step_start_gateway,
    "start_openviking": step_start_openviking,
    "ingest": step_ingest,
    "snapshot_ingest": step_snapshot_ingest,
    "qa": step_qa,
    "judge": step_judge,
    "stat": step_stat,
    "archive": step_archive,
}


def main():
    parser = argparse.ArgumentParser(
        description="LoCoMo Benchmark - OpenClaw MemCore unified runner"
    )
    parser.add_argument(
        "--config", default="config.toml",
        help="Path to TOML config file (default: config.toml)",
    )
    parser.add_argument(
        "--only", default=None,
        help="Comma-separated list of steps to run (e.g. 'ingest,qa')",
    )
    parser.add_argument(
        "--skip", default=None,
        help="Comma-separated list of steps to skip (e.g. 'judge,archive')",
    )
    parser.add_argument(
        "--generate-config-only", action="store_true",
        help="Only generate openclaw.json from config, then exit",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from a previous interrupted run (skips clean, keeps existing data)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show steps without executing",
    )
    args = parser.parse_args()

    config_path = os.path.join(SCRIPT_DIR, args.config) if not os.path.isabs(args.config) else args.config
    if not os.path.exists(config_path):
        print(f"ERROR: Config file not found: {config_path}")
        print(f"  Copy config.toml to config.local.toml and edit it.")
        sys.exit(1)

    cfg = load_config(config_path)
    memory_mode = cfg.get("memory", {}).get("mode", "none")
    print(f"Config: {config_path}")
    print(f"Name:   {cfg['general']['name']}")
    print(f"Agent:  {cfg['general']['agent_id']}")
    print(f"Memory: {memory_mode}")

    if args.generate_config_only:
        generate_openclaw_json(cfg)
        return

    steps_cfg = cfg.get("steps", {})
    if args.only:
        active_steps = [s.strip() for s in args.only.split(",")]
    else:
        active_steps = [s for s in ALL_STEPS if steps_cfg.get(s, True)]

    if args.resume:
        skip_on_resume = {"clean", "snapshot_ingest"}
        active_steps = [s for s in active_steps if s not in skip_on_resume]
        print("[RESUME MODE] Skipping clean/snapshot_ingest, services will restart, continuing from last checkpoint")

    if args.skip:
        skip = {s.strip() for s in args.skip.split(",")}
        active_steps = [s for s in active_steps if s not in skip]

    print(f"Steps:  {' → '.join(active_steps)}")
    print()

    if args.dry_run:
        print("[DRY RUN] Would execute these steps:")
        for s in active_steps:
            print(f"  - {s}")
        return

    if "start_gateway" not in active_steps:
        generate_openclaw_json(cfg)

    start = time.time()
    for step_name in active_steps:
        fn = STEP_MAP.get(step_name)
        if fn:
            step_start = time.time()
            fn(cfg)
            elapsed = time.time() - step_start
            print(f"[DONE] {step_name} ({elapsed:.1f}s)")
        else:
            print(f"[WARN] Unknown step: {step_name}")

    total = time.time() - start

    if _openviking_proc and _openviking_proc.poll() is None:
        print("\n  Stopping OpenViking launched by this script...")
        _openviking_proc.terminate()
        try:
            _openviking_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _openviking_proc.kill()

    if _gateway_proc and _gateway_proc.poll() is None:
        print("  Stopping gateway launched by this script...")
        _gateway_proc.terminate()
        try:
            _gateway_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _gateway_proc.kill()

    print(f"\n{'='*60}")
    print(f"  All done! Total time: {total:.1f}s ({total/60:.1f}min)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
