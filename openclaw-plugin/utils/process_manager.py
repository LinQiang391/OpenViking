"""
进程管理工具：启动、停止、检查进程状态
"""

import logging
import os
import signal
import subprocess
import time
from typing import Dict, List, Optional, Union

import psutil

from config.settings import COMMAND_TIMEOUT

logger = logging.getLogger(__name__)


class ProcessManager:
    """统一的进程管理器"""

    @staticmethod
    def run_command(
        cmd: Union[List[str], str],
        *,
        timeout: int = COMMAND_TIMEOUT,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        capture: bool = True,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        if isinstance(cmd, str):
            cmd = cmd.split()

        merged_env = {**os.environ, **(env or {})}
        logger.debug("exec: %s (cwd=%s)", " ".join(cmd), cwd)
        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=merged_env,
            check=check,
        )

    @staticmethod
    def start_background(
        cmd: Union[List[str], str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        log_path: Optional[str] = None,
    ) -> subprocess.Popen:
        if isinstance(cmd, str):
            cmd = cmd.split()

        merged_env = {**os.environ, **(env or {})}
        stdout = open(log_path, "w") if log_path else subprocess.DEVNULL
        stderr = subprocess.STDOUT if log_path else subprocess.DEVNULL

        logger.info("background: %s -> %s", " ".join(cmd), log_path or "/dev/null")
        return subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=merged_env,
            start_new_session=True,
        )

    @staticmethod
    def is_port_listening(port: int) -> bool:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                return True
        return False

    @staticmethod
    def wait_for_port(port: int, timeout: int = 30, interval: int = 2) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ProcessManager.is_port_listening(port):
                logger.info("port %d is listening", port)
                return True
            time.sleep(interval)
        logger.warning("port %d not listening after %ds", port, timeout)
        return False

    @staticmethod
    def kill_by_name(pattern: str, sig: int = signal.SIGTERM, wait: float = 5.0) -> int:
        killed = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if pattern in cmdline:
                    logger.info("killing pid %d (%s)", proc.pid, cmdline[:80])
                    proc.send_signal(sig)
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed and wait > 0:
            time.sleep(wait)
        return killed

    @staticmethod
    def find_process(pattern: str) -> List[psutil.Process]:
        result = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if pattern in cmdline:
                    result.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return result

    @staticmethod
    def wait_process_exit(pid: int, timeout: float = 10.0) -> bool:
        try:
            proc = psutil.Process(pid)
            proc.wait(timeout=timeout)
            return True
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            return False

    @staticmethod
    def get_pid_from_port(port: int) -> Optional[int]:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                return conn.pid
        return None

    @staticmethod
    def kill_by_port(port: int, sig: int = signal.SIGTERM, wait: float = 5.0) -> bool:
        pid = ProcessManager.get_pid_from_port(port)
        if pid is None:
            return True
        try:
            proc = psutil.Process(pid)
            logger.info("killing pid %d listening on port %d", pid, port)
            proc.send_signal(sig)
            if wait > 0:
                proc.wait(timeout=wait)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
        except psutil.AccessDenied:
            logger.warning("access denied killing pid %d", pid)
            return False
        return not ProcessManager.is_port_listening(port)
