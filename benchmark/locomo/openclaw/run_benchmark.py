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
ALL_STEPS = ["clean", "ingest", "snapshot_ingest", "qa", "judge", "stat", "archive"]
INGEST_STAGING_DIR = os.path.join(SCRIPT_DIR, ".ingest_sessions_staging")


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

    if emb.get("enabled", True):
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

    out_path = os.path.join(openclaw_dir, "openclaw.json")
    os.makedirs(openclaw_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(oc, f, indent=2, ensure_ascii=False)
    print(f"[OK] Generated {out_path}")
    return out_path


def step_clean(cfg: dict):
    agent_id = cfg["general"]["agent_id"]
    openclaw_dir = expand_path(cfg["general"]["openclaw_dir"])
    archive_dir = os.path.join(SCRIPT_DIR, "archive")
    run_cmd(
        ["python", "test/clean_openclaw.py",
         "--openclaw-dir", openclaw_dir,
         "--agent-id", agent_id,
         "--archive-dir", archive_dir,
         "-y"],
        f"Clean environment (agent={agent_id}, archive first)"
    )


def step_ingest(cfg: dict):
    ing = cfg["ingest"]
    gw = cfg["gateway"]
    gen = cfg["general"]
    data_file = ing.get("data_file", gen.get("data_file", "../data/locomo10.json"))

    cmd = [
        "python", "eval.py", "ingest", data_file,
        "--token", gw["token"],
        "--agent-id", gen["agent_id"],
        "--user", ing.get("user", "eval-1"),
    ]
    if ing.get("compact", True):
        cmd.append("--compact")
    if ing.get("clear_record", False):
        cmd.append("--clear-ingest-record")

    sample = ing.get("sample", -1)
    if sample >= 0:
        cmd.extend(["--sample", str(sample)])

    run_cmd(cmd, "Ingest conversations")


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
    for f in sorted(sessions_dir.glob("*.jsonl.ingest.*")):
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
    print(f"Config: {config_path}")
    print(f"Name:   {cfg['general']['name']}")
    print(f"Agent:  {cfg['general']['agent_id']}")

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
        print("[RESUME MODE] Skipping clean & snapshot_ingest, continuing from last checkpoint")

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
    print(f"\n{'='*60}")
    print(f"  All done! Total time: {total:.1f}s ({total/60:.1f}min)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
