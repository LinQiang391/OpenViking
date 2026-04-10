"""
Ingest phase: call existing scripts to inject data into memory backends.

- OpenViking: calls openclaw/import_to_ov.py
- OpenClaw (memcore): calls openclaw/eval.py ingest
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from ..config import EvalConfig, ScenarioConfig

log = logging.getLogger(__name__)

_OPENCLAW_SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve() / "openclaw"


class IngestPhase:
    """Orchestrate data ingestion by calling existing scripts."""

    def __init__(self, config: EvalConfig, scenario: ScenarioConfig, output_dir: str, gateway_url: str):
        self.config = config
        self.scenario = scenario
        self.output_dir = output_dir
        self.gateway_url = gateway_url

    def run(self):
        os.makedirs(self.output_dir, exist_ok=True)
        ingest_cfg = self.scenario.ingest

        if ingest_cfg.openviking:
            self._ingest_openviking()

        if ingest_cfg.openclaw:
            self._ingest_openclaw()

    def _ingest_openviking(self):
        """Call import_to_ov.py to inject into OpenViking."""
        log.info(f"[{self.scenario.name}] Ingesting into OpenViking ...")

        script = str(_OPENCLAW_SCRIPT_DIR / "import_to_ov.py")
        cmd = [
            sys.executable, script,
            "--input", self.config.data.locomo_json,
            "--openviking-url", self.config.openviking.url,
            "--success-csv", os.path.join(self.output_dir, "import_success.csv"),
            "--error-log", os.path.join(self.output_dir, "import_errors.log"),
        ]

        if self.config.openviking.no_user_agent_id:
            cmd.append("--no-user-agent-id")

        if self.config.data.sample is not None:
            cmd.extend(["--sample", str(self.config.data.sample)])

        if self.config.data.sessions:
            cmd.extend(["--sessions", self.config.data.sessions])

        log.info(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode != 0:
            log.error(f"  import_to_ov.py failed with exit code {result.returncode}")
        else:
            log.info(f"  OpenViking ingest complete.")

    def _ingest_openclaw(self):
        """Call eval.py ingest to inject into OpenClaw (memcore)."""
        log.info(f"[{self.scenario.name}] Ingesting into OpenClaw (memcore) ...")

        script = str(_OPENCLAW_SCRIPT_DIR / "eval.py")
        cmd = [
            sys.executable, script,
            "ingest",
            self.config.data.locomo_json,
            "--base-url", self.gateway_url,
            "--token", self.config.openclaw.token,
            "--agent-id", self.config.openclaw.default_agent_id,
            "--user", f"{self.scenario.qa.user_prefix}-ingest",
        ]

        if self.config.data.sample is not None:
            cmd.extend(["--sample", str(self.config.data.sample)])

        if self.config.data.sessions:
            cmd.extend(["--sessions", self.config.data.sessions])

        log.info(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode != 0:
            log.error(f"  eval.py ingest failed with exit code {result.returncode}")
        else:
            log.info(f"  OpenClaw ingest complete.")
