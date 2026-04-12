"""
Archive a complete QA test run.

Copies all test artifacts (OpenClaw memory, sessions, config, QA results, etc.)
into a timestamped archive directory for reproducibility.

Usage:
    python test/archive_run.py --name "memcore-embedding-v1"
    python test/archive_run.py --name "memcore-no-embedding" --openclaw-dir C:\\Users\\johnny\\.openclaw
    python test/archive_run.py --name "memcore-embedding-v1" --agent-id locomo-eval --dry-run
"""

import argparse
import json
import os
import shutil
import time
from pathlib import Path


def copy_tree(src: Path, dst: Path, dry_run: bool = False):
    """Recursively copy a directory tree, skipping files that can't be read."""
    if not src.exists():
        print(f"  [SKIP] source not found: {src}")
        return 0
    count = 0
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            target = dst / rel
            if dry_run:
                print(f"  [DRY] {item} -> {target}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, target)
                except (PermissionError, OSError) as e:
                    print(f"  [WARN] cannot copy {item}: {e}")
                    continue
            count += 1
    return count


def copy_file(src: Path, dst: Path, dry_run: bool = False):
    """Copy a single file."""
    if not src.exists():
        print(f"  [SKIP] file not found: {src}")
        return False
    if dry_run:
        print(f"  [DRY] {src} -> {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
        return True
    except (PermissionError, OSError) as e:
        print(f"  [WARN] cannot copy {src}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Archive a QA test run")
    parser.add_argument(
        "--name", required=True,
        help="Descriptive name for this run (e.g. 'memcore-embedding-v1')",
    )
    parser.add_argument(
        "--openclaw-dir",
        default=os.path.expanduser("~/.openclaw"),
        help="OpenClaw home directory, default: ~/.openclaw",
    )
    parser.add_argument(
        "--agent-id", default="locomo-eval",
        help="Agent ID, default: locomo-eval",
    )
    parser.add_argument(
        "--result-dir",
        default=None,
        help="Path to result directory, default: ./result",
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        help="Base archive directory, default: ./archive",
    )
    parser.add_argument(
        "--ingest-sessions-dir",
        default=None,
        help="Directory containing ingest-phase session files (for separate ingest/qa archival)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without copying")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent  # benchmark/locomo/openclaw
    openclaw_dir = Path(args.openclaw_dir)
    agent_id = args.agent_id
    result_dir = Path(args.result_dir) if args.result_dir else script_dir / "result"
    archive_base = Path(args.archive_dir) if args.archive_dir else script_dir / "archive"

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_name = f"{timestamp}_{args.name}"
    archive_path = archive_base / run_name

    print(f"Archive: {archive_path}")
    print(f"OpenClaw dir: {openclaw_dir}")
    print(f"Agent: {agent_id}")
    print(f"Result dir: {result_dir}")
    if args.dry_run:
        print("[DRY RUN MODE]")
    print()

    total_files = 0

    # 1. OpenClaw config
    print("=== OpenClaw Config ===")
    dst = archive_path / "openclaw"
    count = 0
    for f in ["openclaw.json"]:
        if copy_file(openclaw_dir / f, dst / f, args.dry_run):
            count += 1
    agent_config_dir = openclaw_dir / "agents" / agent_id / "agent"
    if agent_config_dir.exists():
        for f in agent_config_dir.iterdir():
            if f.is_file():
                if copy_file(f, dst / "agent" / f.name, args.dry_run):
                    count += 1
    print(f"  Config: {count} file(s)")
    total_files += count

    # 2. Memory files (workspace)
    print("\n=== Memory Files ===")
    workspace_dir = openclaw_dir / f"workspace-{agent_id}"
    if not workspace_dir.exists():
        workspace_dir = openclaw_dir / "workspace"
    memory_dst = archive_path / "memory"
    memory_dir = workspace_dir / "memory"
    count = copy_tree(memory_dir, memory_dst / "files", args.dry_run)
    # Also copy MEMORY.md if exists
    memory_md = workspace_dir / "MEMORY.md"
    if memory_md.exists():
        if copy_file(memory_md, memory_dst / "MEMORY.md", args.dry_run):
            count += 1
    print(f"  Memory: {count} file(s)")
    total_files += count

    # 3. Memory index (SQLite)
    print("\n=== Memory Index ===")
    sqlite_path = openclaw_dir / "memory" / f"{agent_id}.sqlite"
    if copy_file(sqlite_path, archive_path / "memory" / f"{agent_id}.sqlite", args.dry_run):
        total_files += 1
        size_mb = sqlite_path.stat().st_size / 1024 / 1024
        print(f"  Index: {agent_id}.sqlite ({size_mb:.1f} MB)")
    else:
        print("  Index: not found")

    # 4. Sessions (auto-separate ingest vs QA by filename tag)
    print("\n=== Sessions ===")
    sessions_dir = openclaw_dir / "agents" / agent_id / "sessions"

    if args.ingest_sessions_dir:
        ingest_src = Path(args.ingest_sessions_dir)
        ingest_dst = archive_path / "sessions" / "ingest"
        qa_dst = archive_path / "sessions" / "qa"

        ingest_count = copy_tree(ingest_src, ingest_dst, args.dry_run)
        print(f"  Ingest sessions (from staging): {ingest_count} file(s)")
        total_files += ingest_count

        qa_count = copy_tree(sessions_dir, qa_dst, args.dry_run)
        print(f"  QA sessions: {qa_count} file(s)")
        total_files += qa_count
    else:
        ingest_dst = archive_path / "sessions" / "ingest"
        qa_dst = archive_path / "sessions" / "qa"
        other_dst = archive_path / "sessions"
        ingest_count = qa_count = other_count = 0

        if sessions_dir.exists():
            for f in sessions_dir.rglob("*"):
                if not f.is_file():
                    continue
                name = f.name
                if ".ingest." in name:
                    dst = ingest_dst
                    ingest_count += 1
                elif ".qa." in name:
                    dst = qa_dst
                    qa_count += 1
                else:
                    dst = other_dst
                    other_count += 1

                if not args.dry_run:
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst / name)
                else:
                    print(f"  [DRY] {f} -> {dst / name}")

        if ingest_count or qa_count:
            print(f"  Ingest sessions: {ingest_count} file(s)")
            print(f"  QA sessions: {qa_count} file(s)")
            if other_count:
                print(f"  Other sessions: {other_count} file(s)")
            total_files += ingest_count + qa_count + other_count
        else:
            print(f"  Sessions: {other_count} file(s)")
            total_files += other_count

    # 5. QA result CSVs and other output files
    print("\n=== Result Files ===")
    result_dst = archive_path / "result"
    count = 0
    if result_dir.exists():
        for f in result_dir.iterdir():
            if f.is_file():
                if copy_file(f, result_dst / f.name, args.dry_run):
                    count += 1
    print(f"  Results: {count} file(s)")
    total_files += count

    # 6. Ingest record
    print("\n=== Ingest Record ===")
    ingest_record = script_dir / ".ingest_record.json"
    if copy_file(ingest_record, archive_path / ".ingest_record.json", args.dry_run):
        total_files += 1
        print("  Ingest record: copied")
    else:
        print("  Ingest record: not found")

    # 7. Write run metadata
    print("\n=== Run Metadata ===")
    meta = {
        "name": args.name,
        "timestamp": timestamp,
        "agent_id": agent_id,
        "openclaw_dir": str(openclaw_dir),
        "result_dir": str(result_dir),
        "archive_path": str(archive_path),
    }
    # Read openclaw.json for config snapshot
    config_path = openclaw_dir / "openclaw.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            meta["openclaw_config"] = {
                "model": config.get("agents", {}).get("defaults", {}).get("model", {}),
                "memorySearch": config.get("agents", {}).get("defaults", {}).get("memorySearch", {}),
            }
            api_key = meta["openclaw_config"].get("memorySearch", {}).get("remote", {}).get("apiKey", "")
            if api_key:
                meta["openclaw_config"]["memorySearch"]["remote"]["apiKey"] = api_key[:8] + "..."
        except Exception:
            pass

    # Read memory status
    meta["memory_status"] = {}
    try:
        import subprocess
        result = subprocess.run(
            ["openclaw", "memory", "status", "--agent", agent_id, "--json"],
            capture_output=True, text=True, timeout=15, shell=True
        )
        if result.returncode == 0:
            status = json.loads(result.stdout)
            if status:
                s = status[0].get("status", {})
                meta["memory_status"] = {
                    "files": s.get("files"),
                    "chunks": s.get("chunks"),
                    "dirty": s.get("dirty"),
                    "vector_dims": s.get("vector", {}).get("dims"),
                    "model": s.get("model"),
                }
    except Exception:
        pass

    meta_path = archive_path / "run_meta.json"
    if not args.dry_run:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        total_files += 1
    print(f"  Metadata: {meta_path}")

    print(f"\n{'=' * 40}")
    print(f"Total: {total_files} file(s) archived to {archive_path}")
    if args.dry_run:
        print("[DRY RUN - no files were actually copied]")


if __name__ == "__main__":
    main()
