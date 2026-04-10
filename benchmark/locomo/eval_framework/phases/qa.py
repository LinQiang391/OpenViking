"""
QA phase: call existing eval.py qa to run QA questions against OpenClaw.

Directly invokes openclaw/eval.py in qa mode with scenario-specific parameters
for session isolation (user prefix, output path).
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from ..config import EvalConfig, ScenarioConfig

log = logging.getLogger(__name__)

_OPENCLAW_SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve() / "openclaw"


class QAPhase:
    """Run QA questions by calling eval.py qa."""

    def __init__(
        self,
        config: EvalConfig,
        scenario: ScenarioConfig,
        output_dir: str,
        gateway_url: str,
    ):
        self.config = config
        self.scenario = scenario
        self.output_dir = output_dir
        self.gateway_url = gateway_url

    def run(self) -> str:
        """Run QA phase and return the path to results CSV."""
        os.makedirs(self.output_dir, exist_ok=True)

        csv_base = os.path.join(self.output_dir, "qa_results")
        csv_path = f"{csv_base}.csv"

        script = str(_OPENCLAW_SCRIPT_DIR / "eval.py")
        cmd = [
            sys.executable, script,
            "qa",
            self.config.data.locomo_json,
            "--base-url", self.gateway_url,
            "--token", self.config.openclaw.token,
            "--agent-id", self.config.openclaw.default_agent_id,
            "--user", self.scenario.qa.user_prefix,
            "--parallel", str(self.scenario.qa.parallel),
            "--output", csv_base,
        ]

        if self.config.data.sample is not None:
            cmd.extend(["--sample", str(self.config.data.sample)])

        log.info(f"[{self.scenario.name}] Running QA evaluation ...")
        log.info(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode != 0:
            log.error(f"  eval.py qa failed with exit code {result.returncode}")
        else:
            log.info(f"  QA complete. Results: {csv_path}")

        return csv_path
