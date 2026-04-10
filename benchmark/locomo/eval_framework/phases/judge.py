"""
Judge phase: call existing judge.py to grade QA answers.

Directly invokes openclaw/judge.py with scenario-specific CSV path.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from ..config import EvalConfig, ScenarioConfig

log = logging.getLogger(__name__)

_OPENCLAW_SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve() / "openclaw"


class JudgePhase:
    """Grade QA results by calling judge.py."""

    def __init__(self, config: EvalConfig, scenario: ScenarioConfig, output_dir: str):
        self.config = config
        self.scenario = scenario
        self.output_dir = output_dir

    def run(self):
        csv_path = os.path.join(self.output_dir, "qa_results.csv")
        if not os.path.exists(csv_path):
            log.warning(f"[{self.scenario.name}] No QA results CSV found at {csv_path}")
            return

        script = str(_OPENCLAW_SCRIPT_DIR / "judge.py")
        cmd = [
            sys.executable, script,
            "--input", csv_path,
            "--base-url", self.config.judge.base_url,
            "--token", self.config.judge.api_key,
            "--model", self.config.judge.model,
            "--parallel", str(self.config.execution.judge_parallel),
        ]

        log.info(f"[{self.scenario.name}] Running judge ...")
        log.info(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode != 0:
            log.error(f"  judge.py failed with exit code {result.returncode}")
        else:
            log.info(f"  Judge complete.")
