"""
Clean OpenClaw session records and memory files.

Archives existing data before deletion to prevent data loss.

Usage:
    python clean_openclaw.py                          # Use default ~/.openclaw
    python clean_openclaw.py --openclaw-dir ~/my-claw # Custom OpenClaw directory
    python clean_openclaw.py --sessions-only          # Only clear sessions
    python clean_openclaw.py --memory-only            # Only clear memory
    python clean_openclaw.py --no-archive             # Skip archiving (dangerous)
    python clean_openclaw.py --dry-run                # Preview without deleting
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def get_default_openclaw_dir() -> Path:
    return Path.home() / ".openclaw"


def _has_data_to_archive(openclaw_dir: Path, agent_id: str) -> bool:
    """Check if there is any data worth archiving."""
    sessions_dir = openclaw_dir / "agents" / agent_id / "sessions"
    if sessions_dir.exists():
        jsonl_files = list(sessions_dir.glob("*.jsonl")) + list(sessions_dir.glob("*.jsonl.*"))
        if jsonl_files:
            return True
        sessions_json = sessions_dir / "sessions.json"
        if sessions_json.exists() and sessions_json.stat().st_size > 2:
            return True

    workspaces = [openclaw_dir / "workspace"]
    if agent_id != "main":
        workspaces.append(openclaw_dir / f"workspace-{agent_id}")
    for ws in workspaces:
        memory_md = ws / "MEMORY.md"
        if memory_md.exists():
            return True
        memory_dir = ws / "memory"
        if memory_dir.exists() and any(memory_dir.rglob("*.md")):
            return True

    memory_store_dir = openclaw_dir / "memory"
    if memory_store_dir.exists() and any(memory_store_dir.glob(f"{agent_id}*")):
        return True

    return False


def archive_before_clean(
    openclaw_dir: Path, agent_id: str, archive_base: Path, dry_run: bool
) -> str | None:
    """Archive all current data into a timestamped directory. Returns archive path or None."""
    if not _has_data_to_archive(openclaw_dir, agent_id):
        print("  No data to archive, skipping.\n")
        return None

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archive_path = archive_base / f"{timestamp}_pre-clean"

    print(f"  Archive target: {archive_path}")
    total = 0

    # Sessions
    sessions_dir = openclaw_dir / "agents" / agent_id / "sessions"
    if sessions_dir.exists():
        dst = archive_path / "sessions"
        for f in list(sessions_dir.glob("*.jsonl")) + list(sessions_dir.glob("*.jsonl.*")) + [sessions_dir / "sessions.json"]:
            if f.exists() and f.is_file():
                if not dry_run:
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst / f.name)
                total += 1

    # Memory files (workspace)
    workspaces = [openclaw_dir / "workspace"]
    if agent_id != "main":
        workspaces.append(openclaw_dir / f"workspace-{agent_id}")
    for ws in workspaces:
        memory_md = ws / "MEMORY.md"
        if memory_md.exists():
            dst = archive_path / "memory"
            if not dry_run:
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(memory_md, dst / "MEMORY.md")
            total += 1
        memory_dir = ws / "memory"
        if memory_dir.exists():
            for f in memory_dir.rglob("*.md"):
                rel = f.relative_to(ws)
                dst = archive_path / "memory" / "files" / rel.parent.name / f.name
                if not dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
                total += 1

    # Memory index (SQLite)
    memory_store_dir = openclaw_dir / "memory"
    if memory_store_dir.exists():
        for f in memory_store_dir.glob(f"{agent_id}*"):
            if f.is_file():
                dst = archive_path / "memory"
                if not dry_run:
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst / f.name)
                total += 1

    # OpenClaw config
    oc_json = openclaw_dir / "openclaw.json"
    if oc_json.exists():
        dst = archive_path / "openclaw"
        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(oc_json, dst / "openclaw.json")
        total += 1

    agent_config_dir = openclaw_dir / "agents" / agent_id / "agent"
    if agent_config_dir.exists():
        for f in agent_config_dir.iterdir():
            if f.is_file():
                dst = archive_path / "openclaw" / "agent"
                if not dry_run:
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst / f.name)
                total += 1

    # Write archive metadata
    if not dry_run:
        meta = {
            "type": "pre-clean-archive",
            "timestamp": timestamp,
            "agent_id": agent_id,
            "openclaw_dir": str(openclaw_dir),
            "files_archived": total,
        }
        meta_path = archive_path / "archive_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"  {prefix}Archived {total} file(s) to {archive_path}\n")
    return str(archive_path)


def clean_sessions(openclaw_dir: Path, agent_id: str, dry_run: bool) -> int:
    """Remove session JSONL files and reset sessions.json. Returns count of removed files."""
    sessions_dir = openclaw_dir / "agents" / agent_id / "sessions"
    if not sessions_dir.exists():
        print(f"  Sessions directory not found: {sessions_dir}")
        return 0

    removed = 0

    jsonl_files = list(sessions_dir.glob("*.jsonl")) + list(sessions_dir.glob("*.jsonl.*"))
    for f in jsonl_files:
        print(f"  {'[DRY-RUN] ' if dry_run else ''}Remove: {f.name} ({f.stat().st_size} bytes)")
        if not dry_run:
            f.unlink()
        removed += 1

    sessions_json = sessions_dir / "sessions.json"
    if sessions_json.exists():
        size = sessions_json.stat().st_size
        print(f"  {'[DRY-RUN] ' if dry_run else ''}Reset: sessions.json ({size} bytes -> {{}})")
        if not dry_run:
            sessions_json.write_text("{}", encoding="utf-8")

    return removed


def clean_memory_files(openclaw_dir: Path, agent_id: str, dry_run: bool) -> int:
    """Remove memory Markdown files from workspace(s). Returns count of removed files."""
    removed = 0

    workspaces = [openclaw_dir / "workspace"]
    if agent_id != "main":
        workspaces.append(openclaw_dir / f"workspace-{agent_id}")

    for workspace in workspaces:
        if not workspace.exists():
            continue

        print(f"  Scanning: {workspace}")

        memory_md = workspace / "MEMORY.md"
        if memory_md.exists():
            print(f"  {'[DRY-RUN] ' if dry_run else ''}Remove: MEMORY.md ({memory_md.stat().st_size} bytes)")
            if not dry_run:
                memory_md.unlink()
            removed += 1

        memory_dir = workspace / "memory"
        if memory_dir.exists():
            md_files = list(memory_dir.rglob("*.md"))
            for f in md_files:
                rel = f.relative_to(workspace)
                print(f"  {'[DRY-RUN] ' if dry_run else ''}Remove: {rel} ({f.stat().st_size} bytes)")
                if not dry_run:
                    f.unlink()
                removed += 1

            if not dry_run and memory_dir.exists():
                for dirpath, dirnames, filenames in os.walk(memory_dir, topdown=False):
                    p = Path(dirpath)
                    if not any(p.iterdir()):
                        p.rmdir()
                        print(f"  Remove empty dir: {p.relative_to(workspace)}")

    return removed


def clean_memory_index(openclaw_dir: Path, agent_id: str, dry_run: bool) -> int:
    """Remove memory SQLite index files. Returns count of removed files."""
    memory_store_dir = openclaw_dir / "memory"
    removed = 0

    if not memory_store_dir.exists():
        return 0

    sqlite_files = list(memory_store_dir.glob(f"{agent_id}*"))
    for f in sqlite_files:
        if f.is_file():
            print(f"  {'[DRY-RUN] ' if dry_run else ''}Remove index: {f.name} ({f.stat().st_size} bytes)")
            if not dry_run:
                f.unlink()
            removed += 1

    return removed


def main():
    parser = argparse.ArgumentParser(description="Clean OpenClaw session records and memory files")
    parser.add_argument(
        "--openclaw-dir",
        type=str,
        default=None,
        help=f"OpenClaw base directory (default: {get_default_openclaw_dir()})",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default="main",
        help="Agent ID to clean (default: main)",
    )
    parser.add_argument(
        "--archive-dir",
        type=str,
        default=None,
        help="Base directory for pre-clean archives (default: ./archive next to eval.py)",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip archiving before clean (WARNING: data will be permanently lost)",
    )
    parser.add_argument(
        "--sessions-only",
        action="store_true",
        help="Only clear session records",
    )
    parser.add_argument(
        "--memory-only",
        action="store_true",
        help="Only clear memory files and index",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without actually removing files",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    openclaw_dir = Path(args.openclaw_dir).expanduser() if args.openclaw_dir else get_default_openclaw_dir()

    if not openclaw_dir.exists():
        print(f"Error: OpenClaw directory not found: {openclaw_dir}", file=sys.stderr)
        sys.exit(1)

    script_dir = Path(__file__).resolve().parent.parent  # benchmark/locomo/openclaw
    archive_base = Path(args.archive_dir) if args.archive_dir else script_dir / "archive"

    print(f"OpenClaw directory: {openclaw_dir}")
    print(f"Agent ID: {args.agent_id}")
    if args.dry_run:
        print("Mode: DRY-RUN (no files will be deleted)")
    print()

    clean_sessions_flag = not args.memory_only
    clean_memory_flag = not args.sessions_only

    if not args.yes and not args.dry_run:
        targets = []
        if clean_sessions_flag:
            targets.append("sessions")
        if clean_memory_flag:
            targets.append("memory files + index")
        archive_note = " (data will be archived first)" if not args.no_archive else " (NO archive!)"
        confirm = input(f"This will delete {' and '.join(targets)}{archive_note}. Continue? [y/N] ")
        if confirm.lower() not in ("y", "yes"):
            print("Cancelled.")
            sys.exit(0)

    # Archive existing data before cleaning
    if not args.no_archive:
        print("=== Archiving existing data ===")
        archive_path = archive_before_clean(openclaw_dir, args.agent_id, archive_base, args.dry_run)
        if archive_path:
            print(f"  Data safely archived. Proceeding with clean.\n")

    total_removed = 0

    if clean_sessions_flag:
        print("=== Cleaning sessions ===")
        count = clean_sessions(openclaw_dir, args.agent_id, args.dry_run)
        total_removed += count
        print(f"  Sessions: {count} file(s) {'would be ' if args.dry_run else ''}removed\n")

    if clean_memory_flag:
        print("=== Cleaning memory files ===")
        count = clean_memory_files(openclaw_dir, args.agent_id, args.dry_run)
        total_removed += count
        print(f"  Memory files: {count} file(s) {'would be ' if args.dry_run else ''}removed\n")

        print("=== Cleaning memory index ===")
        count = clean_memory_index(openclaw_dir, args.agent_id, args.dry_run)
        total_removed += count
        print(f"  Index files: {count} file(s) {'would be ' if args.dry_run else ''}removed\n")

    print(f"Total: {total_removed} file(s) {'would be ' if args.dry_run else ''}removed")
    if not args.dry_run:
        print("Done. Restart openclaw gateway to pick up changes.")


if __name__ == "__main__":
    main()
