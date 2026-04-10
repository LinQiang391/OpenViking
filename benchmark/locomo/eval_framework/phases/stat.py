"""
Statistics phase: call existing stat_judge_result.py and compute extended stats.

Invokes openclaw/stat_judge_result.py for the basic summary, then reads the CSV
to produce the ScenarioStats dataclass needed for cross-scenario comparison.
"""

import csv
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import EvalConfig, ScenarioConfig

log = logging.getLogger(__name__)

_OPENCLAW_SCRIPT_DIR = Path(__file__).parent.parent.parent.resolve() / "openclaw"


@dataclass
class CategoryStats:
    correct: int = 0
    wrong: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0


@dataclass
class ScenarioStats:
    scenario_name: str
    description: str = ""

    total_questions: int = 0
    graded: int = 0
    correct: int = 0
    wrong: int = 0

    qa_input_tokens: int = 0
    qa_output_tokens: int = 0
    qa_cache_read_tokens: int = 0
    qa_cache_write_tokens: int = 0
    qa_total_tokens: int = 0

    ingest_embedding_tokens: int = 0
    ingest_vlm_tokens: int = 0
    ingest_total_tokens: int = 0
    ingest_sessions: int = 0

    category_stats: dict[str, CategoryStats] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.graded if self.graded > 0 else 0.0

    @property
    def grand_total_tokens(self) -> int:
        return self.qa_total_tokens + self.ingest_total_tokens

    @property
    def avg_qa_input(self) -> float:
        return self.qa_input_tokens / self.total_questions if self.total_questions > 0 else 0.0

    @property
    def avg_qa_output(self) -> float:
        return self.qa_output_tokens / self.total_questions if self.total_questions > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "scenario_name": self.scenario_name,
            "description": self.description,
            "accuracy": self.accuracy,
            "correct": self.correct,
            "wrong": self.wrong,
            "graded": self.graded,
            "total_questions": self.total_questions,
            "token_usage": {
                "qa": {
                    "input": self.qa_input_tokens,
                    "output": self.qa_output_tokens,
                    "cache_read": self.qa_cache_read_tokens,
                    "cache_write": self.qa_cache_write_tokens,
                    "total": self.qa_total_tokens,
                },
                "ingest": {
                    "embedding": self.ingest_embedding_tokens,
                    "vlm": self.ingest_vlm_tokens,
                    "total": self.ingest_total_tokens,
                    "sessions": self.ingest_sessions,
                },
                "grand_total": self.grand_total_tokens,
            },
            "category_accuracy": {
                cat: {
                    "correct": cs.correct,
                    "total": cs.total,
                    "accuracy": cs.accuracy,
                }
                for cat, cs in sorted(self.category_stats.items())
            },
        }


class StatPhase:
    """Compute statistics by calling stat_judge_result.py and parsing CSVs."""

    def __init__(self, config: EvalConfig, scenario: ScenarioConfig, output_dir: str):
        self.config = config
        self.scenario = scenario
        self.output_dir = output_dir

    def run(self) -> ScenarioStats:
        qa_csv = os.path.join(self.output_dir, "qa_results.csv")
        import_csv = os.path.join(self.output_dir, "import_success.csv")

        # Call the existing stat script for its summary.txt output
        if os.path.exists(qa_csv):
            script = str(_OPENCLAW_SCRIPT_DIR / "stat_judge_result.py")
            cmd = [
                sys.executable, script,
                "--input", qa_csv,
                "--import-csv", import_csv,
            ]
            log.info(f"[{self.scenario.name}] Running stat_judge_result.py ...")
            subprocess.run(cmd, capture_output=False, text=True)

        # Build ScenarioStats by reading CSVs directly (for comparison report)
        stats = ScenarioStats(
            scenario_name=self.scenario.name,
            description=self.scenario.description,
        )

        if os.path.exists(qa_csv):
            self._read_qa_csv(qa_csv, stats)

        if os.path.exists(import_csv):
            self._read_import_csv(import_csv, stats)

        log.info(
            f"[{self.scenario.name}] Stats: "
            f"accuracy={stats.accuracy:.2%} ({stats.correct}/{stats.graded}) "
            f"total_tokens={stats.grand_total_tokens:,}"
        )
        return stats

    @staticmethod
    def _read_qa_csv(csv_path: str, stats: ScenarioStats):
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat = row.get("category", "")
                if cat == "5":
                    continue

                stats.total_questions += 1
                result = row.get("result", "").strip().upper()
                if result == "CORRECT":
                    stats.correct += 1
                    stats.graded += 1
                elif result == "WRONG":
                    stats.wrong += 1
                    stats.graded += 1

                try:
                    stats.qa_input_tokens += int(row.get("input_tokens", 0) or 0)
                    stats.qa_output_tokens += int(row.get("output_tokens", 0) or 0)
                    stats.qa_cache_read_tokens += int(row.get("cacheRead", 0) or 0)
                    stats.qa_cache_write_tokens += int(row.get("cacheWrite", 0) or 0)
                    stats.qa_total_tokens += int(row.get("total_tokens", 0) or 0)
                except (ValueError, TypeError):
                    pass

                if cat:
                    if cat not in stats.category_stats:
                        stats.category_stats[cat] = CategoryStats()
                    cs = stats.category_stats[cat]
                    cs.total += 1
                    if result == "CORRECT":
                        cs.correct += 1
                    elif result == "WRONG":
                        cs.wrong += 1

    @staticmethod
    def _read_import_csv(csv_path: str, stats: ScenarioStats):
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stats.ingest_sessions += 1
                try:
                    stats.ingest_embedding_tokens += int(row.get("embedding_tokens", 0) or 0)
                    stats.ingest_vlm_tokens += int(row.get("vlm_tokens", 0) or 0)
                    stats.ingest_total_tokens += int(row.get("total_tokens", 0) or 0)
                except (ValueError, TypeError):
                    pass
