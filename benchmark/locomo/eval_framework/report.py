"""
Report generator: produce per-scenario summaries and cross-scenario comparison reports.

Output formats: txt, csv, json.
"""

import csv
import json
import logging
import os
import time

from .config import EvalConfig
from .phases.stat import ScenarioStats

log = logging.getLogger(__name__)


class ReportGenerator:
    """Generate comparison reports across multiple scenario results."""

    def __init__(self, config: EvalConfig, all_stats: list[ScenarioStats]):
        self.config = config
        self.all_stats = all_stats
        self.output_dir = config.execution.output_dir

    def generate(self):
        os.makedirs(self.output_dir, exist_ok=True)
        report_cfg = self.config.report

        if "txt" in report_cfg.format:
            self._generate_txt()
        if "json" in report_cfg.format:
            self._generate_json()
        if "csv" in report_cfg.format:
            self._generate_csv()

    def _generate_txt(self):
        path = os.path.join(self.output_dir, "comparison_report.txt")
        lines = []

        lines.append("=" * 60)
        lines.append("  LoCoMo 多场景对比报告")
        lines.append(f"  Project: {self.config.project_name}")
        lines.append(f"  Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.all_stats:
            total_q = self.all_stats[0].total_questions
            lines.append(f"  Data: {os.path.basename(self.config.data.locomo_json)} | {total_q} questions")
        lines.append("=" * 60)

        # Accuracy comparison table
        baseline = self.all_stats[0] if self.all_stats else None
        lines.append("")
        lines.append("=== 总体准确率对比 ===")
        lines.append("")

        header = f"{'场景':<36} {'准确率':>8} {'正确/总计':>10} {'较基线提升':>10}"
        lines.append(header)
        lines.append("-" * len(header))

        for stats in self.all_stats:
            name = stats.scenario_name
            acc = f"{stats.accuracy:.2%}"
            ratio = f"{stats.correct}/{stats.graded}"
            if baseline and stats is not baseline and baseline.accuracy > 0:
                delta = stats.accuracy - baseline.accuracy
                improvement = f"+{delta:.2%}" if delta > 0 else f"{delta:.2%}"
            elif stats is baseline:
                improvement = "(基线)"
            else:
                improvement = "-"
            lines.append(f"{name:<36} {acc:>8} {ratio:>10} {improvement:>10}")

        # Category breakdown
        if self.config.report.include_category_breakdown:
            all_categories = set()
            for s in self.all_stats:
                all_categories.update(s.category_stats.keys())

            if all_categories:
                lines.append("")
                lines.append("=== 按 Category 分类准确率 ===")
                for cat in sorted(all_categories):
                    lines.append(f"")
                    lines.append(f"Category {cat}:")
                    for stats in self.all_stats:
                        cs = stats.category_stats.get(cat)
                        if cs:
                            lines.append(
                                f"  {stats.scenario_name:<34} "
                                f"{cs.accuracy:.2%} ({cs.correct}/{cs.total})"
                            )
                        else:
                            lines.append(f"  {stats.scenario_name:<34} N/A")

        # Token cost comparison
        if self.config.report.include_token_breakdown:
            lines.append("")
            lines.append("=== Token 成本对比 ===")
            lines.append("")
            header = f"{'场景':<36} {'QA Input':>12} {'QA Output':>12} {'Ingest':>12} {'总计':>12}"
            lines.append(header)
            lines.append("-" * len(header))
            for stats in self.all_stats:
                lines.append(
                    f"{stats.scenario_name:<36} "
                    f"{stats.qa_input_tokens:>12,} "
                    f"{stats.qa_output_tokens:>12,} "
                    f"{stats.ingest_total_tokens:>12,} "
                    f"{stats.grand_total_tokens:>12,}"
                )

            # Cost-effectiveness
            lines.append("")
            lines.append("=== 性价比分析 ===")
            lines.append("")
            header = f"{'场景':<36} {'准确率':>8} {'总 Tokens':>12} {'每1%准确率Token':>16}"
            lines.append(header)
            lines.append("-" * len(header))
            for stats in self.all_stats:
                cost_per_pct = (
                    stats.grand_total_tokens / (stats.accuracy * 100)
                    if stats.accuracy > 0
                    else 0
                )
                lines.append(
                    f"{stats.scenario_name:<36} "
                    f"{stats.accuracy:>8.2%} "
                    f"{stats.grand_total_tokens:>12,} "
                    f"{cost_per_pct:>16,.0f}"
                )

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        log.info(f"Comparison report (txt): {path}")

    def _generate_json(self):
        path = os.path.join(self.output_dir, "comparison_report.json")
        report = {
            "project_name": self.config.project_name,
            "run_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "data": {
                "locomo_json": os.path.basename(self.config.data.locomo_json),
                "total_questions": self.all_stats[0].total_questions if self.all_stats else 0,
            },
            "scenarios": [s.to_dict() for s in self.all_stats],
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        log.info(f"Comparison report (json): {path}")

    def _generate_csv(self):
        path = os.path.join(self.output_dir, "comparison_report.csv")
        fieldnames = [
            "scenario", "description", "accuracy", "correct", "graded",
            "total_questions", "qa_input_tokens", "qa_output_tokens",
            "qa_cache_read", "qa_total_tokens", "ingest_embedding_tokens",
            "ingest_vlm_tokens", "ingest_total_tokens", "grand_total_tokens",
        ]

        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for stats in self.all_stats:
                writer.writerow({
                    "scenario": stats.scenario_name,
                    "description": stats.description,
                    "accuracy": f"{stats.accuracy:.4f}",
                    "correct": stats.correct,
                    "graded": stats.graded,
                    "total_questions": stats.total_questions,
                    "qa_input_tokens": stats.qa_input_tokens,
                    "qa_output_tokens": stats.qa_output_tokens,
                    "qa_cache_read": stats.qa_cache_read_tokens,
                    "qa_total_tokens": stats.qa_total_tokens,
                    "ingest_embedding_tokens": stats.ingest_embedding_tokens,
                    "ingest_vlm_tokens": stats.ingest_vlm_tokens,
                    "ingest_total_tokens": stats.ingest_total_tokens,
                    "grand_total_tokens": stats.grand_total_tokens,
                })
        log.info(f"Comparison report (csv): {path}")
