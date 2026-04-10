"""
Configuration loader for the LoCoMo evaluation framework.

Loads config.yaml, resolves environment variable references (${VAR}),
and provides typed access to all configuration sections.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${VAR} references in strings to environment variable values."""
    if isinstance(value, str):
        def _replace(match):
            var_name = match.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                raise ValueError(
                    f"Environment variable '{var_name}' is not set. "
                    f"Please set it or replace '${{var_name}}' in config.yaml."
                )
            return env_val
        return re.sub(r"\$\{(\w+)}", _replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


@dataclass
class DataConfig:
    locomo_json: str
    sample: int | None = None
    sessions: str | None = None
    skip_category_5: bool = True


@dataclass
class ProfileConfig:
    profile_name: str
    gateway_port: int
    model: str | None = None
    plugins: dict = field(default_factory=dict)
    openviking_plugin: dict = field(default_factory=dict)


@dataclass
class ModelProviderConfig:
    name: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass
class OpenClawConfig:
    token: str
    default_agent_id: str = "locomo-eval"
    default_model: str = ""
    model_provider: ModelProviderConfig | None = None
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)


@dataclass
class OpenVikingConfig:
    url: str = "http://localhost:1933"
    no_user_agent_id: bool = True


@dataclass
class JudgeConfig:
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    api_key: str = ""
    model: str = "doubao-seed-2-0-pro-260215"
    temperature: float = 0


@dataclass
class IngestConfig:
    openclaw: bool = False
    openviking: bool = False


@dataclass
class QAConfig:
    user_prefix: str = "eval"
    parallel: int = 10


@dataclass
class ScenarioConfig:
    name: str
    description: str
    enabled: bool
    profile: str
    ingest: IngestConfig
    qa: QAConfig
    profile_config: ProfileConfig | None = None


@dataclass
class ExecutionConfig:
    judge_parallel: int = 40
    ingest_wait_seconds: int = 60
    retry_count: int = 2
    output_dir: str = "./eval_results"
    env_file: str = "~/.openviking_benchmark_env"


@dataclass
class ReportConfig:
    format: list[str] = field(default_factory=lambda: ["txt", "csv", "json"])
    compare_scenarios: bool = True
    include_token_breakdown: bool = True
    include_category_breakdown: bool = True


@dataclass
class CleanupConfig:
    require_confirmation: bool = True


@dataclass
class EvalConfig:
    project_name: str
    data: DataConfig
    openclaw: OpenClawConfig
    openviking: OpenVikingConfig
    judge: JudgeConfig
    scenarios: list[ScenarioConfig]
    execution: ExecutionConfig
    report: ReportConfig
    cleanup: CleanupConfig
    config_dir: Path = field(default_factory=lambda: Path("."))


def _parse_profile(name: str, raw: dict) -> ProfileConfig:
    return ProfileConfig(
        profile_name=raw.get("profile_name", name),
        gateway_port=raw.get("gateway_port", 18800),
        model=raw.get("model"),
        plugins=raw.get("plugins", {}),
        openviking_plugin=raw.get("openviking_plugin", {}),
    )


def _parse_scenario(raw: dict, profiles: dict[str, ProfileConfig]) -> ScenarioConfig:
    ingest_raw = raw.get("ingest", {})
    qa_raw = raw.get("qa", {})
    profile_key = raw.get("profile", raw["name"])
    return ScenarioConfig(
        name=raw["name"],
        description=raw.get("description", ""),
        enabled=raw.get("enabled", True),
        profile=profile_key,
        ingest=IngestConfig(
            openclaw=ingest_raw.get("openclaw", False),
            openviking=ingest_raw.get("openviking", False),
        ),
        qa=QAConfig(
            user_prefix=qa_raw.get("user_prefix", "eval"),
            parallel=qa_raw.get("parallel", 10),
        ),
        profile_config=profiles.get(profile_key),
    )


def load_config(config_path: str) -> EvalConfig:
    """Load and parse the evaluation configuration from a YAML file."""
    config_path = Path(config_path).resolve()
    config_dir = config_path.parent

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Load env file if specified
    env_file_raw = raw.get("execution", {}).get("env_file", "~/.openviking_benchmark_env")
    env_file = Path(os.path.expanduser(env_file_raw))
    if env_file.exists():
        load_dotenv(env_file)

    # Resolve environment variables
    raw = _resolve_env_vars(raw)

    # Resolve relative data path
    data_raw = raw.get("data", {})
    locomo_json = data_raw.get("locomo_json", "../data/locomo10.json")
    if not os.path.isabs(locomo_json):
        locomo_json = str((config_dir / locomo_json).resolve())

    # Parse profiles
    profiles_raw = raw.get("openclaw", {}).get("profiles", {})
    profiles = {name: _parse_profile(name, cfg) for name, cfg in profiles_raw.items()}

    # Parse openclaw config
    oc_raw = raw.get("openclaw", {})
    mp_raw = oc_raw.get("model_provider")
    model_provider = None
    if mp_raw:
        model_provider = ModelProviderConfig(
            name=mp_raw.get("name", ""),
            base_url=mp_raw.get("base_url", ""),
            api_key=mp_raw.get("api_key", ""),
        )
    openclaw = OpenClawConfig(
        token=oc_raw.get("token", ""),
        default_agent_id=oc_raw.get("default_agent_id", "locomo-eval"),
        default_model=oc_raw.get("default_model", ""),
        model_provider=model_provider,
        profiles=profiles,
    )

    # Parse scenarios
    scenarios_raw = raw.get("scenarios", [])
    scenarios = [_parse_scenario(s, profiles) for s in scenarios_raw]

    # Parse execution
    exec_raw = raw.get("execution", {})
    output_dir = exec_raw.get("output_dir", "./eval_results")
    if not os.path.isabs(output_dir):
        output_dir = str((config_dir / output_dir).resolve())

    execution = ExecutionConfig(
        judge_parallel=exec_raw.get("judge_parallel", 40),
        ingest_wait_seconds=exec_raw.get("ingest_wait_seconds", 60),
        retry_count=exec_raw.get("retry_count", 2),
        output_dir=output_dir,
        env_file=env_file_raw,
    )

    # Parse other sections
    ov_raw = raw.get("openviking", {})
    judge_raw = raw.get("judge", {})
    report_raw = raw.get("report", {})
    cleanup_raw = raw.get("cleanup", {})

    return EvalConfig(
        project_name=raw.get("project_name", "LoCoMo_Eval"),
        data=DataConfig(
            locomo_json=locomo_json,
            sample=data_raw.get("sample"),
            sessions=data_raw.get("sessions"),
            skip_category_5=data_raw.get("skip_category_5", True),
        ),
        openclaw=openclaw,
        openviking=OpenVikingConfig(
            url=ov_raw.get("url", "http://localhost:1933"),
            no_user_agent_id=ov_raw.get("no_user_agent_id", True),
        ),
        judge=JudgeConfig(
            base_url=judge_raw.get("base_url", "https://ark.cn-beijing.volces.com/api/v3"),
            api_key=judge_raw.get("api_key", ""),
            model=judge_raw.get("model", "doubao-seed-2-0-pro-260215"),
            temperature=judge_raw.get("temperature", 0),
        ),
        scenarios=scenarios,
        execution=execution,
        report=ReportConfig(
            format=report_raw.get("format", ["txt", "csv", "json"]),
            compare_scenarios=report_raw.get("compare_scenarios", True),
            include_token_breakdown=report_raw.get("include_token_breakdown", True),
            include_category_breakdown=report_raw.get("include_category_breakdown", True),
        ),
        cleanup=CleanupConfig(
            require_confirmation=cleanup_raw.get("require_confirmation", True),
        ),
        config_dir=config_dir,
    )
