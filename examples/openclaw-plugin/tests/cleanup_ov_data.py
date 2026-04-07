"""
OpenViking data directory cleanup script.

Wipes the OpenViking server's data directory (sessions, memories, vector DB,
tenant metadata, etc.) to restore a clean-slate state.

The data directory location is read from ov.conf (storage.workspace), defaulting
to ~/.openviking/data.

The script checks whether the OpenViking server is running and refuses to
proceed unless --stop is given (to auto-stop) or the server is already down.

Usage:
  # Preview what will be deleted
  python cleanup_ov_data.py

  # Delete data (server must be stopped first)
  python cleanup_ov_data.py --force

  # Auto-stop server, delete data, then auto-restart
  python cleanup_ov_data.py --force --stop --restart

  # Use a custom ov.conf path
  python cleanup_ov_data.py --conf ~/.openviking/ov.conf --force
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


DEFAULT_OV_CONF = Path.home() / ".openviking" / "ov.conf"
DEFAULT_DATA_DIR = Path.home() / ".openviking" / "data"


def load_ov_conf(conf_path: Path) -> dict:
    if not conf_path.exists():
        return {}
    with open(conf_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_data_dir(conf: dict) -> Path:
    workspace = conf.get("storage", {}).get("workspace")
    if workspace:
        return Path(workspace)
    return DEFAULT_DATA_DIR


def get_pid_file(data_dir: Path) -> Path:
    return data_dir / ".openviking.pid"


def read_pid(data_dir: Path) -> Optional[int]:
    pid_file = get_pid_file(data_dir)
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def is_process_running(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=5,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def stop_server(pid: int) -> bool:
    print(f"  Stopping OpenViking server (PID {pid})...")
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)

        for _ in range(30):
            time.sleep(0.5)
            if not is_process_running(pid):
                print(f"  Server stopped.")
                return True
        print(f"  WARNING: Server may still be running after timeout.")
        return False
    except Exception as e:
        print(f"  Error stopping server: {e}")
        return False


def start_server(conf_path: Path) -> bool:
    print(f"  Restarting OpenViking server...")
    try:
        openviking_exe = shutil.which("openviking")
        if openviking_exe:
            cmd = [openviking_exe, "serve"]
            if conf_path != DEFAULT_OV_CONF:
                cmd.extend(["--config", str(conf_path)])
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            print(f"  Server restart initiated.")
            return True
        else:
            print("  WARNING: 'openviking' not found on PATH. Please restart manually.")
            return False
    except Exception as e:
        print(f"  Error restarting: {e}")
        return False


def dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def list_data_contents(data_dir: Path) -> None:
    if not data_dir.exists():
        print(f"  Data directory does not exist: {data_dir}")
        return

    size = dir_size_mb(data_dir)
    print(f"\n  Data directory: {data_dir}")
    print(f"  Total size: {size:.1f} MB\n")

    print(f"  Contents:")
    for entry in sorted(data_dir.iterdir()):
        if entry.is_dir():
            sub_size = dir_size_mb(entry)
            sub_count = sum(1 for _ in entry.rglob("*") if _.is_file())
            print(f"    {entry.name:30s}  {sub_size:8.1f} MB  ({sub_count} files)")
        else:
            fsize = entry.stat().st_size
            print(f"    {entry.name:30s}  {fsize / 1024:.1f} KB")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up OpenViking server data directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview
  python cleanup_ov_data.py

  # Delete (server must be stopped)
  python cleanup_ov_data.py --force

  # Auto-stop, delete, auto-restart
  python cleanup_ov_data.py --force --stop --restart

  # Custom config
  python cleanup_ov_data.py --conf /path/to/ov.conf --force
""",
    )

    parser.add_argument("--conf", default=str(DEFAULT_OV_CONF), help=f"Path to ov.conf (default: {DEFAULT_OV_CONF})")
    parser.add_argument("--force", action="store_true", help="Actually delete the data directory")
    parser.add_argument("--stop", action="store_true", help="Auto-stop the server if running")
    parser.add_argument("--restart", action="store_true", help="Auto-restart after cleanup (requires --stop)")
    parser.add_argument("--keep-log", action="store_true", help="Preserve the log subdirectory")

    args = parser.parse_args()
    conf_path = Path(args.conf).expanduser()

    sep = "=" * 62
    print(f"\n{sep}")
    print("  OpenViking Data Cleanup")
    print(sep)

    conf = load_ov_conf(conf_path)
    data_dir = get_data_dir(conf)
    port = conf.get("server", {}).get("port", 1933)

    print(f"  Config: {conf_path}")
    print(f"  Data:   {data_dir}")
    print(f"  Port:   {port}")

    if not data_dir.exists():
        print(f"\n  Data directory does not exist. Nothing to clean.")
        print(sep)
        return

    list_data_contents(data_dir)

    pid = read_pid(data_dir)
    server_running = pid is not None and is_process_running(pid)

    if server_running:
        print(f"\n  Server status: RUNNING (PID {pid})")
        if not args.force:
            print(f"\n  [DRY-RUN] Would delete: {data_dir}")
            print(f"  Add --force to delete. Server must be stopped first (use --stop).")
            print(sep)
            return
        if not args.stop:
            print(f"\n  ERROR: Server is running. Stop it first or use --stop to auto-stop.")
            print(sep)
            sys.exit(1)
        if not stop_server(pid):
            print(f"  ERROR: Failed to stop server. Aborting.")
            print(sep)
            sys.exit(1)
    else:
        print(f"\n  Server status: STOPPED")

    if not args.force:
        print(f"\n  [DRY-RUN] Would delete: {data_dir}")
        print(f"  Add --force to actually delete.")
        print(sep)
        return

    if args.keep_log:
        log_dir = data_dir / "log"
        log_backup = None
        if log_dir.exists():
            log_backup = data_dir.parent / "_log_backup_tmp"
            if log_backup.exists():
                shutil.rmtree(log_backup)
            shutil.copytree(log_dir, log_backup)

        shutil.rmtree(data_dir)
        print(f"\n  [DELETED] {data_dir}")

        if log_backup and log_backup.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(log_backup, log_dir)
            shutil.rmtree(log_backup)
            print(f"  [KEPT] {log_dir}")
    else:
        shutil.rmtree(data_dir)
        print(f"\n  [DELETED] {data_dir}")

    if args.restart:
        start_server(conf_path)

    print(f"\n  Done. Server will recreate data directory on next startup.")
    print(sep)
    print()


if __name__ == "__main__":
    main()
