"""
Cleanup manager: manually triggered data cleanup for evaluation environments.

All cleanup operations require explicit user confirmation.
Data is never cleaned automatically during the evaluation pipeline.
"""

import csv
import logging
import os
import shutil
from pathlib import Path

from .config import EvalConfig

log = logging.getLogger(__name__)


class CleanupManager:
    """
    Handle cleanup of evaluation data across all backends.

    Cleanup is always manual - it is NOT called during the normal evaluation flow.
    Users must explicitly run `python run_eval.py --cleanup` to trigger it.
    """

    def __init__(self, config: EvalConfig):
        self.config = config

    def cleanup(
        self,
        scenario_name: str | None = None,
        targets: list[str] | None = None,
        dry_run: bool = False,
    ):
        """
        Clean up data for the specified scenario and targets.

        Args:
            scenario_name: Specific scenario to clean, or None for all.
            targets: List of targets to clean:
                     "openclaw-profile", "openclaw-sessions", "openviking",
                     "memcore", "results", "all"
            dry_run: If True, only print what would be deleted.
        """
        targets = targets or ["all"]
        if "all" in targets:
            targets = ["openclaw-profile", "openviking", "results"]

        # Collect summary of what will be deleted
        summary = self._collect_summary(scenario_name, targets)

        if not summary:
            print("Nothing to clean up.")
            return

        print("\n⚠️  以下数据将被永久删除:\n")
        for item in summary:
            print(f"  - {item}")
        print()

        if dry_run:
            print("(dry-run mode, no data was deleted)")
            return

        if self.config.cleanup.require_confirmation:
            confirm = input("确认删除？输入 'yes' 继续，其他输入取消: ")
            if confirm.strip().lower() != "yes":
                print("已取消清理操作。")
                return

        # Execute cleanup
        if "openclaw-profile" in targets:
            self._cleanup_openclaw_profiles(scenario_name)
        if "openclaw-sessions" in targets:
            self._cleanup_openclaw_sessions(scenario_name)
        if "openviking" in targets:
            self._cleanup_openviking_data(scenario_name)
        if "results" in targets:
            self._cleanup_results(scenario_name)

        print("\n✅ 清理完成。")

    def _collect_summary(
        self, scenario_name: str | None, targets: list[str]
    ) -> list[str]:
        items = []
        scenarios = self._get_target_scenarios(scenario_name)

        if "openclaw-profile" in targets:
            for s in scenarios:
                pc = s.profile_config
                if pc:
                    state_dir = Path(os.path.expanduser(f"~/.openclaw-{pc.profile_name}"))
                    if state_dir.exists():
                        items.append(f"OpenClaw profile '{pc.profile_name}': {state_dir}")

        if "openclaw-sessions" in targets:
            for s in scenarios:
                pc = s.profile_config
                if pc:
                    state_dir = Path(os.path.expanduser(f"~/.openclaw-{pc.profile_name}"))
                    sessions_dir = state_dir / "agents"
                    if sessions_dir.exists():
                        items.append(f"OpenClaw sessions in profile '{pc.profile_name}'")

        if "openviking" in targets:
            for s in scenarios:
                if s.ingest.openviking:
                    csv_path = os.path.join(
                        self.config.execution.output_dir, s.name, "import_success.csv"
                    )
                    if os.path.exists(csv_path):
                        count = sum(1 for _ in open(csv_path)) - 1
                        items.append(f"OpenViking sessions ({count} records) for '{s.name}'")

        if "results" in targets:
            for s in scenarios:
                result_dir = os.path.join(self.config.execution.output_dir, s.name)
                if os.path.exists(result_dir):
                    items.append(f"Result files: {result_dir}")

        return items

    def _get_target_scenarios(self, scenario_name: str | None):
        if scenario_name:
            return [s for s in self.config.scenarios if s.name == scenario_name]
        return self.config.scenarios

    def _cleanup_openclaw_profiles(self, scenario_name: str | None):
        from .environment import ProfileManager
        pm = ProfileManager(self.config)
        for s in self._get_target_scenarios(scenario_name):
            if s.profile_config:
                pm.delete_profile(s.profile_config.profile_name)

    def _cleanup_openclaw_sessions(self, scenario_name: str | None):
        for s in self._get_target_scenarios(scenario_name):
            if not s.profile_config:
                continue
            pn = s.profile_config.profile_name
            state_dir = Path(os.path.expanduser(f"~/.openclaw-{pn}"))
            agents_dir = state_dir / "agents"
            if not agents_dir.exists():
                continue

            for agent_name in os.listdir(agents_dir):
                sessions_dir = agents_dir / agent_name / "sessions"
                if not sessions_dir.is_dir():
                    continue
                for f in os.listdir(sessions_dir):
                    if f.endswith(".jsonl"):
                        (sessions_dir / f).unlink()
                        log.info(f"  Removed: {sessions_dir / f}")

    def _cleanup_openviking_data(self, scenario_name: str | None):
        """Delete OpenViking sessions recorded in import_success.csv."""
        try:
            import asyncio
            import openviking as ov
        except ImportError:
            log.warning("openviking SDK not available, skipping OV cleanup")
            return

        async def _do_cleanup():
            client = ov.AsyncHTTPClient(url=self.config.openviking.url)
            await client.initialize()
            try:
                for s in self._get_target_scenarios(scenario_name):
                    if not s.ingest.openviking:
                        continue
                    csv_path = os.path.join(
                        self.config.execution.output_dir, s.name, "import_success.csv"
                    )
                    if not os.path.exists(csv_path):
                        continue
                    with open(csv_path, "r") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            sid = row.get("session_id")
                            if sid:
                                try:
                                    await client.delete_session(sid)
                                    log.info(f"  Deleted OV session: {sid}")
                                except Exception as e:
                                    log.warning(f"  Failed to delete OV session {sid}: {e}")
            finally:
                await client.close()

        asyncio.run(_do_cleanup())

    def _cleanup_results(self, scenario_name: str | None):
        for s in self._get_target_scenarios(scenario_name):
            result_dir = os.path.join(self.config.execution.output_dir, s.name)
            if os.path.exists(result_dir):
                shutil.rmtree(result_dir)
                log.info(f"  Removed result directory: {result_dir}")
