"""
Environment checker and OpenClaw profile manager.

Handles:
- Pre-flight environment checks (OpenClaw CLI, services, data files)
- Profile lifecycle: create, configure plugins, start/stop gateway
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

from .config import EvalConfig, ProfileConfig, ScenarioConfig

log = logging.getLogger(__name__)


class EnvironmentChecker:
    """Validate that all prerequisites are met before running evaluation."""

    def __init__(self, config: EvalConfig):
        self.config = config

    def check_all(self) -> list[str]:
        """Run all checks, return list of error messages (empty = all good)."""
        errors = []
        errors.extend(self._check_openclaw_cli())
        errors.extend(self._check_data_file())
        errors.extend(self._check_openviking_service())
        errors.extend(self._check_tokens())
        return errors

    def _check_openclaw_cli(self) -> list[str]:
        try:
            result = subprocess.run(
                ["openclaw", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip() or result.stderr.strip()
                log.info(f"OpenClaw version: {version}")
                return []
            return [f"openclaw --version failed: {result.stderr.strip()}"]
        except FileNotFoundError:
            return ["openclaw CLI not found. Please install OpenClaw first."]
        except subprocess.TimeoutExpired:
            return ["openclaw --version timed out"]

    def _check_data_file(self) -> list[str]:
        path = self.config.data.locomo_json
        if not os.path.exists(path):
            return [f"LoCoMo data file not found: {path}"]
        return []

    def _check_openviking_service(self) -> list[str]:
        needs_ov = any(
            s.ingest.openviking
            for s in self.config.scenarios
            if s.enabled
        )
        if not needs_ov:
            return []

        url = self.config.openviking.url
        try:
            resp = requests.get(f"{url}/health", timeout=5)
            if resp.status_code == 200:
                log.info(f"OpenViking service healthy at {url}")
                return []
            return [f"OpenViking service returned {resp.status_code} at {url}/health"]
        except requests.ConnectionError:
            return [f"Cannot connect to OpenViking service at {url}"]
        except requests.Timeout:
            return [f"OpenViking service timeout at {url}"]

    def _check_tokens(self) -> list[str]:
        errors = []
        if not self.config.openclaw.token:
            errors.append(
                "OpenClaw token not set. Set OPENCLAW_GATEWAY_TOKEN or configure openclaw.token."
            )
        if not self.config.judge.api_key:
            errors.append(
                "Judge API key not set. Set ARK_API_KEY or configure judge.api_key."
            )
        return errors


class ProfileManager:
    """Manage OpenClaw profiles via `openclaw --profile <name>` CLI."""

    def __init__(self, config: EvalConfig):
        self.config = config

    def _run_openclaw(
        self, profile_name: str, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess:
        cmd = ["openclaw", "--profile", profile_name, *args]
        log.debug(f"Running: {' '.join(cmd)}")
        return subprocess.run(
            cmd, capture_output=True, text=True, check=check, timeout=120,
        )

    def init_profile(self, scenario: ScenarioConfig) -> str:
        """
        Initialize a profile for the given scenario.

        Creates a clean OpenClaw environment if needed, installs and configures
        the required plugins, starts the gateway, and returns the gateway URL.
        """
        pc = scenario.profile_config
        if pc is None:
            raise ValueError(
                f"Scenario '{scenario.name}' references profile '{scenario.profile}' "
                "which is not defined in openclaw.profiles."
            )

        profile_name = pc.profile_name
        port = pc.gateway_port
        state_dir = Path(os.path.expanduser(f"~/.openclaw-{profile_name}"))

        log.info(f"[{scenario.name}] Initializing profile '{profile_name}' ...")

        # Create profile if it doesn't exist
        if not state_dir.exists():
            log.info(f"  Creating new profile (state dir: {state_dir})")
            self._run_openclaw(profile_name, "onboard", check=False)

        # Configure gateway port
        self._run_openclaw(
            profile_name, "config", "set", "gateway.http.port", str(port),
        )

        # Enable responses endpoint
        self._run_openclaw(
            profile_name, "config", "set",
            "gateway.http.endpoints.responses.enabled", "true", "--json",
        )

        # Configure default model
        self._setup_model(profile_name, pc)

        # Setup plugins
        self._setup_plugins(profile_name, pc)

        # Start gateway (stop first if already running)
        self._run_openclaw(profile_name, "gateway", "stop", check=False)

        # Source openviking.env if exists
        env_file = state_dir / "openviking.env"
        extra_env = {}
        if env_file.exists():
            extra_env = self._parse_env_file(env_file)

        env = {**os.environ, **extra_env}
        start_cmd = ["openclaw", "--profile", profile_name, "gateway", "start"]
        subprocess.Popen(start_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for gateway
        gateway_url = f"http://127.0.0.1:{port}"
        self._wait_for_ready(gateway_url, timeout=60)

        # Log profile configuration
        result = self._run_openclaw(
            profile_name, "config", "get", "plugins", check=False,
        )
        log.info(f"  Profile plugins:\n{result.stdout.strip()}")

        return gateway_url

    def _setup_model(self, profile_name: str, pc: ProfileConfig):
        """Configure the default LLM model for the profile."""
        model = pc.model or self.config.openclaw.default_model
        if not model:
            return

        log.info(f"  Setting default model: {model}")
        self._run_openclaw(
            profile_name, "config", "set",
            "agents.defaults.model.primary", model,
        )

        # Configure custom model provider if specified
        mp = self.config.openclaw.model_provider
        if mp and mp.name:
            provider_name = mp.name
            if mp.base_url:
                self._run_openclaw(
                    profile_name, "config", "set",
                    f"models.providers.{provider_name}.baseUrl", mp.base_url,
                )
            if mp.api_key:
                self._run_openclaw(
                    profile_name, "config", "set",
                    f"models.providers.{provider_name}.apiKey", mp.api_key,
                )

    def _setup_plugins(self, profile_name: str, pc: ProfileConfig):
        plugins = pc.plugins

        # contextEngine (OpenViking)
        context_engine = plugins.get("context_engine")
        if context_engine == "openviking":
            state_dir = os.path.expanduser(f"~/.openclaw-{profile_name}")
            extensions_dir = os.path.join(state_dir, "extensions", "@openclaw", "openviking")

            if not os.path.exists(extensions_dir):
                log.info(f"  Installing OpenViking plugin into profile '{profile_name}'")
                subprocess.run(
                    ["ov-install", "--workdir", state_dir, "-y"],
                    check=True, timeout=300,
                )

            self._run_openclaw(profile_name, "plugins", "enable", "openviking", check=False)
            self._run_openclaw(
                profile_name, "config", "set",
                "plugins.slots.contextEngine", "openviking",
            )

            for key, value in pc.openviking_plugin.items():
                str_val = str(value).lower() if isinstance(value, bool) else str(value)
                is_json = isinstance(value, bool)
                args = [
                    "config", "set",
                    f"plugins.entries.openviking.config.{key}", str_val,
                ]
                if is_json:
                    args.append("--json")
                self._run_openclaw(profile_name, *args)
        else:
            self._run_openclaw(
                profile_name, "config", "set", "plugins.slots.contextEngine", "",
                check=False,
            )

        # Ensure no external memory plugin is loaded (memcore is built-in)
        self._run_openclaw(
            profile_name, "config", "set", "plugins.slots.memory", "",
            check=False,
        )

    def stop_profile(self, profile_name: str):
        log.info(f"Stopping gateway for profile '{profile_name}'")
        self._run_openclaw(profile_name, "gateway", "stop", check=False)

    def stop_all_profiles(self):
        for scenario in self.config.scenarios:
            if scenario.enabled and scenario.profile_config:
                self.stop_profile(scenario.profile_config.profile_name)

    def dump_profile(self, profile_name: str) -> str:
        result = self._run_openclaw(
            profile_name, "config", "get", "plugins", check=False,
        )
        return result.stdout.strip()

    def dump_all_profiles(self) -> dict[str, str]:
        dumps = {}
        for scenario in self.config.scenarios:
            if scenario.enabled and scenario.profile_config:
                pn = scenario.profile_config.profile_name
                state_dir = Path(os.path.expanduser(f"~/.openclaw-{pn}"))
                if state_dir.exists():
                    dumps[pn] = self.dump_profile(pn)
                else:
                    dumps[pn] = "(not yet created)"
        return dumps

    def delete_profile(self, profile_name: str):
        self.stop_profile(profile_name)
        state_dir = Path(os.path.expanduser(f"~/.openclaw-{profile_name}"))
        if state_dir.exists():
            shutil.rmtree(state_dir)
            log.info(f"Deleted profile directory: {state_dir}")

    @staticmethod
    def _parse_env_file(env_file: Path) -> dict[str, str]:
        env = {}
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip().strip('"').strip("'")
        return env

    @staticmethod
    def _wait_for_ready(gateway_url: str, timeout: int = 60):
        log.info(f"  Waiting for gateway at {gateway_url} ...")
        for i in range(timeout):
            try:
                resp = requests.get(f"{gateway_url}/health", timeout=3)
                if resp.status_code == 200:
                    log.info(f"  Gateway ready ({i+1}s)")
                    return
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)
        raise RuntimeError(f"Gateway not ready after {timeout}s at {gateway_url}")
