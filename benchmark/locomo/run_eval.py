#!/usr/bin/env python3
"""
LoCoMo Universal Evaluation Script.

Runs LoCoMo benchmark across multiple memory scenarios (OpenClaw-only,
OpenClaw+MemCore, OpenClaw+OpenViking, OpenClaw+OpenViking+MemCore)
with automatic OpenClaw profile isolation, and produces comparison reports.

Usage:
    python run_eval.py                                    # Run all enabled scenarios
    python run_eval.py --config my_config.yaml            # Use custom config
    python run_eval.py --scenario openclaw_only            # Run single scenario
    python run_eval.py --phase ingest                     # Run only ingest phase
    python run_eval.py --skip-ingest                      # Skip ingest, run qa+judge+stat
    python run_eval.py --report-only                      # Generate reports from existing data
    python run_eval.py --check-env                        # Environment pre-check only
    python run_eval.py --dump-profile                     # Show all OpenClaw profiles
    python run_eval.py --cleanup --target all             # Manual data cleanup
    python run_eval.py --cleanup --target all --dry-run   # Preview cleanup
"""

import argparse
import logging
import os
import sys
import time

# Ensure the eval_framework package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_framework.config import EvalConfig, load_config
from eval_framework.environment import EnvironmentChecker, ProfileManager
from eval_framework.cleanup import CleanupManager
from eval_framework.phases.ingest import IngestPhase
from eval_framework.phases.qa import QAPhase
from eval_framework.phases.judge import JudgePhase
from eval_framework.phases.stat import ScenarioStats, StatPhase
from eval_framework.report import ReportGenerator

log = logging.getLogger("locomo_eval")


def setup_logging(output_dir: str, verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)

    os.makedirs(output_dir, exist_ok=True)
    fh = logging.FileHandler(
        os.path.join(output_dir, "eval.log"), encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logging.getLogger().addHandler(fh)


def cmd_check_env(config: EvalConfig):
    """Run environment pre-checks only."""
    checker = EnvironmentChecker(config)
    errors = checker.check_all()
    if errors:
        print("\n❌ Environment check FAILED:\n")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\n✅ Environment check passed.")
        enabled = [s.name for s in config.scenarios if s.enabled]
        print(f"   Enabled scenarios: {', '.join(enabled)}")


def cmd_dump_profile(config: EvalConfig):
    """Dump all OpenClaw profile configurations."""
    pm = ProfileManager(config)
    dumps = pm.dump_all_profiles()
    for pn, content in dumps.items():
        print(f"\n=== Profile: {pn} ===")
        print(content)


def cmd_cleanup(config: EvalConfig, args):
    """Run manual cleanup."""
    cm = CleanupManager(config)
    targets = args.target.split(",") if args.target else ["all"]
    cm.cleanup(
        scenario_name=args.scenario,
        targets=targets,
        dry_run=args.dry_run,
    )


def cmd_report_only(config: EvalConfig, scenario_filter: str | None):
    """Generate reports from existing result data."""
    all_stats = []
    for scenario in config.scenarios:
        if not scenario.enabled:
            continue
        if scenario_filter and scenario.name != scenario_filter:
            continue
        output_dir = os.path.join(config.execution.output_dir, scenario.name)
        stat_phase = StatPhase(config, scenario, output_dir)
        stats = stat_phase.run()
        all_stats.append(stats)

    if all_stats:
        rg = ReportGenerator(config, all_stats)
        rg.generate()
        print(f"\n✅ Reports generated in {config.execution.output_dir}")
    else:
        print("No scenario results found.")


def run_scenario(
    config: EvalConfig,
    scenario,
    profile_mgr: ProfileManager,
    phases: list[str],
    force_ingest: bool = False,
) -> ScenarioStats | None:
    """Run a single scenario through the specified phases."""
    output_dir = os.path.join(config.execution.output_dir, scenario.name)
    os.makedirs(output_dir, exist_ok=True)

    log.info(f"\n{'='*60}")
    log.info(f"Scenario: {scenario.name}")
    log.info(f"Description: {scenario.description}")
    log.info(f"{'='*60}")

    gateway_url = None

    # Stage 1: Setup profile
    if "setup" in phases:
        log.info("[Stage 1] Setting up OpenClaw profile ...")
        gateway_url = profile_mgr.init_profile(scenario)
        log.info(f"  Gateway URL: {gateway_url}")

        # Save profile snapshot
        if scenario.profile_config:
            dump = profile_mgr.dump_profile(scenario.profile_config.profile_name)
            with open(os.path.join(output_dir, "openclaw_profile.txt"), "w") as f:
                f.write(dump)

    if gateway_url is None and scenario.profile_config:
        gateway_url = f"http://127.0.0.1:{scenario.profile_config.gateway_port}"

    # Stage 2: Ingest
    if "ingest" in phases:
        has_ingest = (
            scenario.ingest.openclaw or
            scenario.ingest.openviking
        )
        if has_ingest:
            log.info("[Stage 2] Ingesting data ...")
            ingest = IngestPhase(config, scenario, output_dir, gateway_url)
            ingest.run()
            wait = config.execution.ingest_wait_seconds
            log.info(f"  Waiting {wait}s for backend indexing ...")
            time.sleep(wait)
        else:
            log.info("[Stage 2] No ingest needed for this scenario.")

    # Stage 3: QA
    if "qa" in phases:
        log.info("[Stage 3] Running QA evaluation ...")
        qa = QAPhase(config, scenario, output_dir, gateway_url)
        qa.run()

    # Stage 4: Judge
    if "judge" in phases:
        log.info("[Stage 4] Running judge ...")
        judge = JudgePhase(config, scenario, output_dir)
        judge.run()

    # Stage 5: Statistics
    stats = None
    if "stat" in phases:
        log.info("[Stage 5] Computing statistics ...")
        stat = StatPhase(config, scenario, output_dir)
        stats = stat.run()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="LoCoMo Universal Evaluation Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config.yaml (default: config.yaml in script directory)",
    )
    parser.add_argument(
        "--scenario", default=None,
        help="Run only the specified scenario",
    )
    parser.add_argument(
        "--phase", default=None,
        help="Run only specified phases (comma-separated): setup,ingest,qa,judge,stat",
    )
    parser.add_argument(
        "--skip-ingest", action="store_true",
        help="Skip the ingest phase",
    )
    parser.add_argument(
        "--force-ingest", action="store_true",
        help="Force re-ingest even if records exist",
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Override sample index from config",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Generate reports from existing results without running evaluation",
    )
    parser.add_argument(
        "--check-env", action="store_true",
        help="Run environment pre-checks only",
    )
    parser.add_argument(
        "--dump-profile", action="store_true",
        help="Show all OpenClaw profile configurations",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Manually clean up evaluation data (requires confirmation)",
    )
    parser.add_argument(
        "--target", default=None,
        help="Cleanup targets (comma-separated): openclaw-profile,openclaw-sessions,openviking,results,all",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview cleanup without actually deleting",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Resolve config path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(script_dir, config_path)

    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        print(f"  Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)

    config = load_config(config_path)

    # Override sample if specified
    if args.sample is not None:
        config.data.sample = args.sample

    setup_logging(config.execution.output_dir, args.verbose)

    # Dispatch commands
    if args.check_env:
        cmd_check_env(config)
        return

    if args.dump_profile:
        cmd_dump_profile(config)
        return

    if args.cleanup:
        cmd_cleanup(config, args)
        return

    if args.report_only:
        cmd_report_only(config, args.scenario)
        return

    # Run evaluation
    checker = EnvironmentChecker(config)
    errors = checker.check_all()
    if errors:
        log.error("Environment check failed:")
        for e in errors:
            log.error(f"  - {e}")
        sys.exit(1)

    # Determine phases
    all_phases = ["setup", "ingest", "qa", "judge", "stat"]
    if args.phase:
        phases = [p.strip() for p in args.phase.split(",")]
    elif args.skip_ingest:
        phases = ["setup", "qa", "judge", "stat"]
    else:
        phases = all_phases

    profile_mgr = ProfileManager(config)
    all_stats: list[ScenarioStats] = []

    try:
        for scenario in config.scenarios:
            if not scenario.enabled:
                continue
            if args.scenario and scenario.name != args.scenario:
                continue

            stats = run_scenario(
                config, scenario, profile_mgr, phases, args.force_ingest,
            )
            if stats:
                all_stats.append(stats)

        # Generate comparison report
        if all_stats and len(all_stats) > 1:
            log.info("\n[Stage 6] Generating comparison report ...")
            rg = ReportGenerator(config, all_stats)
            rg.generate()

        log.info(f"\n{'='*60}")
        log.info("Evaluation complete!")
        log.info(f"Results: {config.execution.output_dir}")
        log.info(f"{'='*60}")

    except KeyboardInterrupt:
        log.warning("\nInterrupted by user.")
    finally:
        log.info("Stopping all profile gateways ...")
        profile_mgr.stop_all_profiles()


if __name__ == "__main__":
    main()
