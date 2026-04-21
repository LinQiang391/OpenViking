"""
OpenClaw Profile 管理：创建隔离的测试 profile，配置模型，安装插件，启停网关。

每个测试用例通过 --profile 参数操作一个独立的 ~/.openclaw-<name>/ 目录，
互不干扰，测试结束后可以直接删除目录完成清理。
"""

import json
import logging
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from config.settings import (
    COMMAND_TIMEOUT,
    GATEWAY_START_TIMEOUT,
    LOG_DIR,
    MAX_RETRIES,
    MESSAGE_TIMEOUT,
    MODEL_PRIMARY,
    MODEL_PROVIDER,
    NODE_PATH,
    OPENVIKING_CONF_CANDIDATES,
    OPENVIKING_PORT,
    PLUGIN_ID,
    PLUGIN_INSTALL_SPEC,
    PLUGIN_PACKAGE,
    PLUGIN_VERSION,
    PROFILE_GATEWAY_PORT,
    PROFILE_GATEWAY_TOKEN,
    PROFILE_GATEWAY_URL,
    PROFILE_HOME,
    PROFILE_NAME,
    PROJECT_ROOT,
    RETRY_DELAY,
)
from utils.config_manager import ConfigManager
from utils.process_manager import ProcessManager

logger = logging.getLogger(__name__)


class ProfileManager:
    """管理 OpenClaw 测试 profile 的完整生命周期。"""

    def __init__(self, profile_name: str = PROFILE_NAME):
        self.profile_name = profile_name
        self.home = os.path.expanduser(f"~/.openclaw-{profile_name}")
        self.config_path = os.path.join(self.home, "openclaw.json")
        self.extensions_dir = os.path.join(self.home, "extensions")
        self.gateway_port = PROFILE_GATEWAY_PORT
        self.gateway_token = PROFILE_GATEWAY_TOKEN
        self.gateway_url = PROFILE_GATEWAY_URL

    # ── CLI 封装 ─────────────────────────────────────────────

    @staticmethod
    def _resolve_openviking_python() -> Optional[str]:
        """从默认 openviking.env 或系统路径中解析安装了 OpenViking 的 Python。"""
        default_env = os.path.expanduser("~/.openclaw/openviking.env")
        if os.path.isfile(default_env):
            import re
            with open(default_env, encoding="utf-8") as f:
                m = re.search(r"OPENVIKING_PYTHON=['\"]([^'\"]+)['\"]", f.read())
                if m:
                    return m.group(1)
        return None

    def _state_env(self) -> Dict[str, str]:
        """构造设置了 OPENCLAW_STATE_DIR 的环境变量。"""
        env = dict(os.environ)
        sep = ";" if os.name == "nt" else ":"
        env["PATH"] = f"{NODE_PATH}{sep}{env.get('PATH', '')}"
        env["OPENCLAW_STATE_DIR"] = self.home
        env["OPENCLAW_CONFIG_PATH"] = self.config_path
        ov_python = self._resolve_openviking_python()
        if ov_python:
            env["OPENVIKING_PYTHON"] = ov_python
        if "CLAWHUB_TOKEN" not in env:
            token = self._resolve_clawhub_token()
            if token:
                env["CLAWHUB_TOKEN"] = token
        return env

    @staticmethod
    def _resolve_clawhub_token() -> Optional[str]:
        """从 clawhub config 读取认证 token，避免未认证下载被限流。"""
        import platform
        if platform.system() == "Windows":
            cfg_path = os.path.join(os.environ.get("APPDATA", ""), "clawhub", "config.json")
        else:
            cfg_path = os.path.expanduser("~/.config/clawhub/config.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                return cfg.get("token", "")
            except Exception:
                pass
        return ""

    def _oc(self, args: List[str], timeout: int = COMMAND_TIMEOUT, **kwargs):
        """执行 openclaw <args...>，通过 OPENCLAW_STATE_DIR 指向 profile 目录。"""
        cmd = ["openclaw"] + args
        return ProcessManager.run_command(cmd, timeout=timeout, env=self._state_env(), **kwargs)

    def _oc_cmd(self, args: List[str], timeout: int = COMMAND_TIMEOUT):
        """执行并返回 (returncode, stdout, stderr)"""
        r = self._oc(args, timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""

    # ── Profile 生命周期 ──────────────────────────────────────

    def exists(self) -> bool:
        return os.path.isdir(self.home)

    def create(self) -> bool:
        """创建 profile（如果不存在则通过 config file 触发自动创建）。"""
        if self.exists():
            logger.info("profile '%s' already exists at %s", self.profile_name, self.home)
            return True

        rc, out, err = self._oc_cmd(["config", "file"])
        logger.info("profile create: config file -> %s", out.strip())

        os.makedirs(self.home, exist_ok=True)
        if not os.path.isfile(self.config_path):
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

        return self.exists()

    def destroy(self) -> bool:
        """删除整个 profile 目录。"""
        if not self.exists():
            logger.info("profile '%s' does not exist, nothing to destroy", self.profile_name)
            return True

        self.stop_gateway()
        time.sleep(1)

        try:
            shutil.rmtree(self.home)
            logger.info("destroyed profile '%s' at %s", self.profile_name, self.home)
            return True
        except Exception as exc:
            logger.error("failed to destroy profile: %s", exc)
            return False

    # ── 配置管理 ──────────────────────────────────────────────

    def read_config(self) -> Dict[str, Any]:
        if os.path.isfile(self.config_path):
            with open(self.config_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def write_config(self, config: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info("wrote profile config: %s", self.config_path)

    def configure_models(
        self,
        primary_model: str = MODEL_PRIMARY,
        provider: Optional[Dict[str, Any]] = None,
    ) -> None:
        """配置模型 provider 和 primary model。"""
        provider = provider or MODEL_PROVIDER
        cfg = self.read_config()

        # agents.defaults.model.primary
        agents = cfg.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        defaults.setdefault("model", {})["primary"] = primary_model
        defaults.setdefault("workspace", os.path.join(self.home, "workspace"))
        defaults["timeoutSeconds"] = 300

        # models.providers (merge with existing, don't overwrite)
        if provider and provider.get("name"):
            provider_name = provider["name"]
            models_cfg = cfg.setdefault("models", {})
            models_cfg["mode"] = "merge"
            providers = models_cfg.setdefault("providers", {})
            existing = providers.get(provider_name, {})
            skip_keys = {"name", "api_key"}
            for k, v in provider.items():
                if k not in skip_keys:
                    existing[k] = v
            # OpenClaw 要求 providers 有 models 数组（每项需要 id + name）
            if "models" not in existing or not existing["models"]:
                model_id = primary_model.split("/", 1)[-1] if "/" in primary_model else primary_model
                existing["models"] = [{"id": model_id, "name": model_id}]
            providers[provider_name] = existing

        self.write_config(cfg)
        logger.info("configured model: primary=%s provider=%s", primary_model, provider.get("name", "default"))

    def configure_auth_from_default(self) -> None:
        """从默认 profile 复制 auth 配置，并注入 volcengine key。"""
        default_home = os.path.expanduser("~/.openclaw")
        default_config = os.path.join(default_home, "openclaw.json")
        if not os.path.isfile(default_config):
            logger.warning("default openclaw config not found, skipping auth copy")
            return

        with open(default_config, encoding="utf-8") as f:
            default_cfg = json.load(f)

        # 1) 复制 openclaw.json 中的 auth 和 models.providers
        cfg = self.read_config()
        if "auth" in default_cfg:
            cfg["auth"] = default_cfg["auth"]
        if "models" in default_cfg and "providers" in default_cfg["models"]:
            models = cfg.setdefault("models", {})
            models["mode"] = "merge"
            models["providers"] = default_cfg["models"]["providers"]

        self.write_config(cfg)

        # 2) 复制 agent 的 auth-profiles.json（API key 实际存储位置）
        src_auth = os.path.join(default_home, "agents", "main", "agent", "auth-profiles.json")
        dst_dir = os.path.join(self.home, "agents", "main", "agent")
        os.makedirs(dst_dir, exist_ok=True)
        dst_auth = os.path.join(dst_dir, "auth-profiles.json")

        auth_data: Dict[str, Any] = {"version": 1, "profiles": {}}
        if os.path.isfile(src_auth):
            with open(src_auth, encoding="utf-8") as f:
                auth_data = json.load(f)
            logger.info("copied auth-profiles.json from default profile")

        # 3) 如果测试使用 volcengine provider，从 ov.conf 获取 api_key 注入
        provider_name = MODEL_PROVIDER.get("name", "")
        if provider_name == "volcengine":
            profiles = auth_data.setdefault("profiles", {})
            key_id = f"{provider_name}:default"
            if key_id not in profiles:
                from utils.config_manager import ConfigManager
                ov_conf_path = ConfigManager.find_config()
                if ov_conf_path:
                    with open(ov_conf_path, encoding="utf-8") as f:
                        ov_cfg = json.load(f)
                    api_key = ov_cfg.get("api_key", "")
                    if not api_key:
                        vlm = ov_cfg.get("vlm", {})
                        api_key = vlm.get("api_key", "")
                    if api_key:
                        profiles[key_id] = {
                            "type": "api_key",
                            "provider": provider_name,
                            "key": api_key,
                        }
                        logger.info("injected %s API key from ov.conf", provider_name)

        with open(dst_auth, "w", encoding="utf-8") as f:
            json.dump(auth_data, f, indent=2)

        logger.info("wrote auth-profiles to %s", dst_auth)

    def configure_gateway(self) -> None:
        """配置 gateway 端口、认证和 HTTP endpoints。"""
        cfg = self.read_config()
        gw = cfg.setdefault("gateway", {})
        gw["port"] = self.gateway_port
        gw["mode"] = "local"
        gw["bind"] = "loopback"
        gw.setdefault("auth", {})["mode"] = "token"
        gw["auth"]["token"] = self.gateway_token
        gw.setdefault("http", {}).setdefault("endpoints", {}).setdefault("responses", {})["enabled"] = True
        gw.setdefault("controlUi", {})["allowInsecureAuth"] = True
        self.write_config(cfg)
        logger.info("configured gateway: port=%d", self.gateway_port)

    def create_isolated_ov_conf(self, ov_port: Optional[int] = None) -> str:
        """从 test_config.json 的 ov_conf 段独立生成隔离的测试专用 ov.conf。

        独立的存储目录和端口，不影响生产 OV 服务。
        不依赖任何模板文件，所有配置来自 test_config.json。
        """
        from config.settings import OV_CONF_TEMPLATE

        port = ov_port or (OPENVIKING_PORT + 100)
        test_data_dir = os.path.join(self.home, "openviking-data")
        test_conf_path = os.path.join(self.home, "ov.conf")

        if OV_CONF_TEMPLATE:
            ov_cfg = ConfigManager.generate_from_test_config(
                port=port,
                data_dir=test_data_dir,
                output_path=test_conf_path,
            )
        else:
            src_conf = None
            for p in OPENVIKING_CONF_CANDIDATES:
                if os.path.isfile(p):
                    src_conf = p
                    break
            if not src_conf:
                raise FileNotFoundError(
                    f"ov.conf not found and test_config.json lacks ov_conf section. "
                    f"Candidates: {OPENVIKING_CONF_CANDIDATES}"
                )
            with open(src_conf, encoding="utf-8") as f:
                ov_cfg_data = json.load(f)
            ov_cfg_data.setdefault("storage", {})["workspace"] = test_data_dir
            ov_cfg_data.setdefault("server", {})["port"] = port
            ov_cfg_data["server"]["host"] = "127.0.0.1"
            with open(test_conf_path, "w", encoding="utf-8") as f:
                json.dump(ov_cfg_data, f, indent=2, ensure_ascii=False)

        self._ov_port = port
        self._ov_conf = test_conf_path
        logger.info("created isolated ov.conf: %s (port=%d, data=%s)", test_conf_path, port, test_data_dir)
        return test_conf_path

    def configure_plugin(
        self,
        mode: str = "local",
        ov_config_path: Optional[str] = None,
        ov_port: Optional[int] = None,
    ) -> None:
        """配置 openviking 插件（local 或 remote 模式）。"""
        # 优先使用 create_isolated_ov_conf 生成的配置
        if ov_config_path is None:
            ov_config_path = getattr(self, "_ov_conf", None)
        if ov_port is None:
            ov_port = getattr(self, "_ov_port", None)
        port = ov_port or OPENVIKING_PORT

        if ov_config_path is None:
            for p in OPENVIKING_CONF_CANDIDATES:
                if os.path.isfile(p):
                    ov_config_path = p
                    break

        cfg = self.read_config()
        plugins = cfg.setdefault("plugins", {})
        allow = plugins.get("allow", [])
        if PLUGIN_ID not in allow:
            allow.append(PLUGIN_ID)
        plugins["allow"] = allow
        plugins.setdefault("slots", {})["contextEngine"] = PLUGIN_ID

        entry = plugins.setdefault("entries", {}).setdefault(PLUGIN_ID, {})
        entry["enabled"] = True
        entry_cfg = entry.setdefault("config", {})
        entry_cfg["mode"] = mode
        entry_cfg["port"] = port

        if mode == "local" and ov_config_path:
            entry_cfg["configPath"] = ov_config_path
        elif mode == "remote":
            entry_cfg["baseUrl"] = f"http://127.0.0.1:{port}"

        self.write_config(cfg)
        logger.info("configured plugin: mode=%s port=%d config=%s", mode, port, ov_config_path)

    # ── 插件安装 ──────────────────────────────────────────────

    def install_plugin(
        self,
        spec: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """通过 clawhub 安装插件到此 profile。

        通过 OPENCLAW_STATE_DIR 让 clawhub 直接安装到 profile 的 extensions 目录，
        不需要从共享目录拷贝。如果 clawhub 安装失败（如 429 限流），回退到本地源目录复制。
        """
        if spec is None:
            ver = version or PLUGIN_VERSION
            spec = f"clawhub:{PLUGIN_PACKAGE}@{ver}"

        rc, out, err = self._oc_cmd(["plugins", "install", "--force", spec])
        result = {
            "success": rc == 0,
            "spec": spec,
            "output": out + (err or ""),
            "error": err if rc != 0 else "",
            "returncode": rc,
        }
        if rc == 0:
            logger.info("plugin installed to profile: %s", spec)
            profile_ext = os.path.join(self.home, "extensions", PLUGIN_ID)
            result["installed_path"] = profile_ext
            result["dir_exists"] = os.path.isdir(profile_ext)
        else:
            logger.warning("clawhub install failed (rc=%d): %s", rc, (err or out)[:200])
            fallback = self._install_plugin_from_source()
            if fallback:
                result["success"] = True
                result["fallback"] = "local-copy"
                result["error"] = ""
                logger.info("plugin installed via local source fallback")
            else:
                logger.error("plugin install failed (rc=%d): %s\n%s", rc, err, out)
        return result

    def _install_plugin_from_source(self) -> bool:
        """Fallback: copy plugin files from the project source tree."""
        from config.settings import BASE_DIR, PLUGIN_DIR_IN_REPO
        candidates = [
            PLUGIN_DIR_IN_REPO,
            os.path.join(BASE_DIR, "..", "examples", "openclaw-plugin"),
        ]
        source_dir = next((d for d in candidates if os.path.isdir(d)), None)
        if not os.path.isdir(source_dir):
            logger.warning("local source not found: %s", source_dir)
            return False

        dest_dir = os.path.join(self.home, "extensions", PLUGIN_ID)
        os.makedirs(dest_dir, exist_ok=True)

        copied = 0
        for item in os.listdir(source_dir):
            src = os.path.join(source_dir, item)
            if os.path.isfile(src) and (item.endswith((".ts", ".js", ".json"))):
                shutil.copy2(src, dest_dir)
                copied += 1
            elif os.path.isdir(src) and item not in ("node_modules", ".git", "__pycache__", "tests"):
                shutil.copytree(src, os.path.join(dest_dir, item), dirs_exist_ok=True)
                copied += 1

        logger.info("copied %d items from %s -> %s", copied, source_dir, dest_dir)

        if copied > 0 and os.path.isfile(os.path.join(dest_dir, "package.json")):
            logger.info("running npm install --production in %s", dest_dir)
            try:
                npm_result = ProcessManager.run_command(
                    ["npm", "install", "--production", "--no-optional"],
                    cwd=dest_dir,
                    timeout=120,
                )
                if npm_result.returncode == 0:
                    logger.info("npm install succeeded in plugin dir")
                else:
                    logger.warning("npm install failed (rc=%d): %s",
                                   npm_result.returncode, (npm_result.stderr or "")[:300])
            except Exception as e:
                logger.warning("npm install error: %s", e)

        return copied > 0

    def verify_plugin_installed(self) -> Dict[str, Any]:
        """验证插件是否已安装。"""
        rc, out, err = self._oc_cmd(["plugins", "list"])
        result = {
            "installed": "openviking" in (out or "").lower(),
            "output": out,
        }
        ext_dir = os.path.join(self.extensions_dir, PLUGIN_ID)
        result["dir_exists"] = os.path.isdir(ext_dir)
        result["index_exists"] = os.path.isfile(os.path.join(ext_dir, "index.ts"))
        return result

    def run_interactive_setup(
        self,
        mode: str = "local",
        config_path: Optional[str] = None,
        port: Optional[int] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """运行 `openclaw openviking setup` 交互式配置。

        Unix: 使用 pexpect 逐步匹配 prompt 并发送回答。
        Windows: 使用 subprocess + stdin 管道预写全部回答。
        """
        cmd = "openclaw openviking setup --reconfigure"
        env = self._state_env()
        result: Dict[str, Any] = {"success": False, "output": "", "steps": []}

        logger.info("interactive setup: running '%s'", cmd)
        logger.info("interactive setup: OPENCLAW_STATE_DIR=%s", env.get("OPENCLAW_STATE_DIR"))

        if os.name == "nt":
            return self._run_interactive_setup_win(
                cmd, env, mode, config_path, port, base_url,
                api_key, agent_id, timeout, result,
            )
        return self._run_interactive_setup_unix(
            cmd, env, mode, config_path, port, base_url,
            api_key, agent_id, timeout, result,
        )

    def _run_interactive_setup_win(
        self,
        cmd: str,
        env: Dict[str, str],
        mode: str,
        config_path: Optional[str],
        port: Optional[int],
        base_url: Optional[str],
        api_key: Optional[str],
        agent_id: Optional[str],
        timeout: int,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Windows: prompt-driven interactive setup via stdout monitoring.

        Node.js readline discards stdin data that arrives between two
        rl.question() calls (emitted as unhandled 'line' events).
        We must wait until the prompt text appears on stdout before
        writing each answer.
        """
        import subprocess as _sp
        import threading
        import re

        if mode == "local":
            cfg_path = config_path or getattr(self, "_ov_conf", "")
            p = str(port) if port else str(getattr(self, "_ov_port", ""))
            prompts_and_answers = [
                (re.compile(r"(mode|模式).*\[", re.IGNORECASE), mode, "mode"),
                (re.compile(r"(config\s*path|配置文件).*\[", re.IGNORECASE), cfg_path, "config_path"),
                (re.compile(r"(port|端口).*\[", re.IGNORECASE), p, "port"),
            ]
            result["steps"] = [
                {"name": "mode", "sent": mode},
                {"name": "config_path", "sent": cfg_path},
                {"name": "port", "sent": p},
            ]
        elif mode == "remote":
            url = base_url or f"http://127.0.0.1:{OPENVIKING_PORT}"
            prompts_and_answers = [
                (re.compile(r"(mode|模式).*\[", re.IGNORECASE), mode, "mode"),
                (re.compile(r"(url|地址).*\[", re.IGNORECASE), url, "base_url"),
                (re.compile(r"(api.?key).*\[", re.IGNORECASE), api_key or "", "api_key"),
                (re.compile(r"(agent.?id).*\[", re.IGNORECASE), agent_id or "", "agent_id"),
            ]
            result["steps"] = [
                {"name": "mode", "sent": mode},
                {"name": "base_url", "sent": url},
                {"name": "api_key", "sent": api_key or ""},
                {"name": "agent_id", "sent": agent_id or ""},
            ]
        else:
            result["error"] = f"unsupported mode: {mode}"
            return result

        logger.info("setup (win): prompt-driven answers: %s",
                     [(name, ans[:40]) for _, ans, name in prompts_and_answers])

        try:
            proc = _sp.Popen(
                cmd.split(),
                stdin=_sp.PIPE,
                stdout=_sp.PIPE,
                stderr=_sp.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                shell=True,
            )

            all_output = []
            answer_idx = 0
            lock = threading.Lock()

            def _stdout_reader():
                nonlocal answer_idx
                buf = ""
                while True:
                    chunk = proc.stdout.read(1)
                    if not chunk:
                        break
                    buf += chunk
                    all_output.append(chunk)
                    with lock:
                        if answer_idx < len(prompts_and_answers):
                            pattern, answer, name = prompts_and_answers[answer_idx]
                            if pattern.search(buf):
                                time.sleep(0.3)
                                try:
                                    proc.stdin.write(answer + "\n")
                                    proc.stdin.flush()
                                    logger.info("setup (win): prompt '%s' detected → sent: %s",
                                                name, answer[:60])
                                    answer_idx += 1
                                    buf = ""
                                except (OSError, BrokenPipeError):
                                    pass

            reader_thread = threading.Thread(target=_stdout_reader, daemon=True)
            reader_thread.start()

            stderr_content = []

            def _stderr_reader():
                while True:
                    chunk = proc.stderr.read(1)
                    if not chunk:
                        break
                    stderr_content.append(chunk)

            stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
            stderr_thread.start()

            reader_thread.join(timeout=timeout)
            stderr_thread.join(timeout=5)

            try:
                proc.stdin.close()
            except OSError:
                pass

            proc.wait(timeout=10)

            stdout_text = "".join(all_output)
            stderr_text = "".join(stderr_content)
            result["output"] = stdout_text + stderr_text
            result["success"] = proc.returncode == 0
            if proc.returncode != 0:
                result["error"] = f"exit code {proc.returncode}"
                logger.error("setup (win) failed (rc=%d): %s", proc.returncode, result["output"][-500:])
            else:
                logger.info("setup (win) completed: %s", result["output"][-500:])
        except _sp.TimeoutExpired:
            proc.kill()
            result["error"] = f"setup timed out after {timeout}s"
            logger.error("setup (win) timeout")
        except Exception as e:
            result["error"] = str(e)
            logger.error("setup (win) failed: %s", e)

        return result

    def _run_interactive_setup_unix(
        self,
        cmd: str,
        env: Dict[str, str],
        mode: str,
        config_path: Optional[str],
        port: Optional[int],
        base_url: Optional[str],
        api_key: Optional[str],
        agent_id: Optional[str],
        timeout: int,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Unix: use pexpect for prompt-by-prompt interaction."""
        import pexpect

        try:
            child = pexpect.spawn(
                "/bin/bash", ["-c", cmd],
                env=env, timeout=timeout, encoding="utf-8",
            )

            def step(name: str, expect_pattern: str, send_value: str):
                child.expect(expect_pattern, timeout=timeout)
                before_text = (child.before or "").strip()
                matched_text = (child.after or "").strip()
                if before_text:
                    logger.info("setup [%s] output before prompt:\n%s", name, before_text)
                logger.info("setup [%s] prompt: %s", name, matched_text)
                logger.info("setup [%s] → sending: %s", name, send_value or "(enter)")
                result["steps"].append({
                    "name": name,
                    "prompt": matched_text,
                    "sent": send_value,
                    "before": before_text,
                })
                child.sendline(send_value)

            step("mode", r"local (?:or|或) remote", mode)

            if mode == "local":
                cfg_path = config_path or getattr(self, "_ov_conf", "")
                step("config_path", r"(?:Config path|配置文件路径)", cfg_path)
                p = str(port) if port else str(getattr(self, "_ov_port", ""))
                step("port", r"(?:Port|端口)", p)
            elif mode == "remote":
                url = base_url or f"http://127.0.0.1:{OPENVIKING_PORT}"
                step("base_url", r"(?:server URL|服务器地址)", url)
                step("api_key", r"API [Kk]ey", api_key or "")
                step("agent_id", r"Agent ID", agent_id or "")

            child.expect(pexpect.EOF, timeout=timeout)
            final_output = (child.before or "").strip()
            result["output"] = final_output
            result["success"] = True
            logger.info("setup final output:\n%s", final_output)
            logger.info("setup completed: %d interactive steps", len(result["steps"]))

        except pexpect.TIMEOUT:
            result["error"] = f"setup timed out after {timeout}s"
            result["output"] = child.before if 'child' in dir() else ""
            logger.error("setup timeout: %s", result.get("output", "")[-300:])
        except pexpect.EOF:
            result["output"] = child.before if 'child' in dir() else ""
            result["success"] = True
            logger.info("setup EOF (completed): output=%s", (result["output"] or "")[-500:])
        except Exception as e:
            result["error"] = str(e)
            logger.error("setup failed: %s", e)

        return result

    def uninstall_plugin(self) -> Dict[str, Any]:
        rc, out, err = self._oc_cmd(["plugins", "uninstall", PLUGIN_ID])
        result = {"success": rc == 0, "output": out, "error": err}
        if rc != 0:
            target = os.path.join(self.extensions_dir, PLUGIN_ID)
            if os.path.isdir(target):
                shutil.rmtree(target)
                result["success"] = True
                result["note"] = "removed manually after CLI uninstall failed"
        return result

    # ── Gateway 管理 ──────────────────────────────────────────

    def start_gateway(self, timeout: int = GATEWAY_START_TIMEOUT) -> bool:
        if ProcessManager.is_port_listening(self.gateway_port):
            logger.info("gateway already listening on %d", self.gateway_port)
            return True

        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("starting profile gateway (attempt %d/%d)", attempt, MAX_RETRIES)

            r = self._oc(["gateway", "start"], timeout=30)
            logger.info("gateway start -> rc=%d stdout=%.200s", r.returncode, r.stdout or "")

            gw_out = (r.stdout or "").lower()
            if "service disabled" in gw_out or "unavailab" in gw_out or "service missing" in gw_out:
                log_path = os.path.join(LOG_DIR, f"gateway_{self.profile_name}.log")
                ProcessManager.start_background(
                    ["openclaw", "gateway"],
                    log_path=log_path,
                    env=self._state_env(),
                )

            if ProcessManager.wait_for_port(self.gateway_port, timeout=timeout):
                logger.info("gateway started on port %d", self.gateway_port)
                return True

            time.sleep(RETRY_DELAY)

        logger.error("failed to start gateway after %d attempts", MAX_RETRIES)
        return False

    def stop_gateway(self) -> bool:
        try:
            self._oc(["gateway", "stop"], timeout=15)
        except Exception:
            pass
        time.sleep(2)
        if ProcessManager.is_port_listening(self.gateway_port):
            ProcessManager.kill_by_port(self.gateway_port, wait=3.0)
        return not ProcessManager.is_port_listening(self.gateway_port)

    def gateway_healthy(self) -> bool:
        import requests as _req
        try:
            resp = _req.get(f"{self.gateway_url}/health", timeout=5)
            return resp.status_code < 500
        except Exception:
            return False

    # ── 会话交互 ──────────────────────────────────────────────

    def send_message(
        self,
        message: str,
        session_id: Optional[str] = None,
        timeout: int = MESSAGE_TIMEOUT,
    ) -> Dict[str, Any]:
        """通过 openclaw agent --message 发送消息。"""
        args = ["agent", "--message", message]
        if session_id:
            args += ["--session-id", session_id]
        args.append("--json")

        rc, out, err = self._oc_cmd(args, timeout=timeout)
        result = {"success": rc == 0, "raw_output": out, "error": err, "text": ""}

        if rc == 0:
            try:
                data = json.loads(out)
                for key in ("output", "message", "content", "text"):
                    if key in data:
                        result["text"] = str(data[key])
                        break
                if not result["text"]:
                    result["text"] = str(data)
                result["data"] = data
            except (json.JSONDecodeError, ValueError):
                result["text"] = out.strip()
        else:
            logger.error("send_message failed (rc=%d): %s", rc, err)

        return result

    def send_message_via_api(
        self,
        message: str,
        session_key: Optional[str] = None,
        user: str = "e2e-test",
        agent_id: str = "e2e-agent",
    ) -> Dict[str, Any]:
        """通过 HTTP API 发送消息（参考 Locomo eval.py 的 send_message）。"""
        import requests as _req

        url = f"{self.gateway_url}/v1/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.gateway_token}",
            "X-OpenClaw-Agent-ID": agent_id,
        }
        if session_key:
            headers["X-OpenClaw-Session-Key"] = session_key

        payload = {
            "model": "openclaw",
            "input": message,
            "stream": False,
        }
        if user:
            payload["user"] = user

        result = {"success": False, "text": "", "usage": {}, "raw": None}
        try:
            resp = _req.post(url, json=payload, headers=headers, timeout=MESSAGE_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            result["raw"] = body
            result["success"] = True
            result["usage"] = body.get("usage", {})

            # Extract text from response
            output = body.get("output", [])
            if isinstance(output, list):
                texts = []
                for item in output:
                    if isinstance(item, dict) and item.get("type") == "message":
                        for c in item.get("content", []):
                            if isinstance(c, dict) and c.get("type") == "output_text":
                                texts.append(c.get("text", ""))
                result["text"] = "\n".join(texts) if texts else json.dumps(body)
            elif isinstance(output, str):
                result["text"] = output
            else:
                result["text"] = json.dumps(body)

        except Exception as exc:
            result["error"] = str(exc)
            logger.error("API send_message failed: %s", exc)

        return result

    # ── Compact / Commit 触发 ────────────────────────────────────

    @staticmethod
    def _session_key_to_ov_id(session_key: str) -> str:
        """将 OpenClaw session key 映射到 OV session ID（与插件逻辑一致）。"""
        import hashlib
        return hashlib.sha256(session_key.encode("utf-8")).hexdigest()

    def _ov_headers(self) -> Dict[str, str]:
        """从隔离 ov.conf 构造 OV API 请求头。"""
        headers: Dict[str, str] = {}
        ov_conf = getattr(self, "_ov_conf", None)
        if ov_conf and os.path.isfile(ov_conf):
            try:
                with open(ov_conf, encoding="utf-8") as f:
                    conf = json.load(f)
                api_key = conf.get("vlm", {}).get("api_key", "") or conf.get("api_key", "")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
            except Exception:
                pass
        return headers

    def trigger_compact(
        self,
        session_key: str,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """通过 Gateway WebSocket RPC 端到端触发 sessions.compact。

        走完整链路: WebSocket → Gateway → sessions.compact → OV Plugin compact → OV commit
        协议参考 memcore 分支 benchmark/locomo/openclaw/eval.py。
        """
        import uuid as _uuid

        import websocket

        ws_url = f"ws://127.0.0.1:{self.gateway_port}/ws"
        result: Dict[str, Any] = {"success": False}

        try:
            ws = websocket.create_connection(ws_url, timeout=timeout)

            # 1) 等待 server 的 connect.challenge 事件
            challenge = json.loads(ws.recv())
            logger.info("compact: server event=%s", challenge.get("event", "unknown"))
            if challenge.get("event") != "connect.challenge":
                result["error"] = f"expected connect.challenge, got: {challenge}"
                ws.close()
                return result

            # 2) 发送 connect 握手请求 (protocol v3, control-ui + allowInsecureAuth)
            connect_id = str(_uuid.uuid4())
            connect_req = {
                "type": "req",
                "id": connect_id,
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": "openclaw-control-ui",
                        "version": "1.0.0",
                        "platform": "linux",
                        "mode": "webchat",
                    },
                    "scopes": [
                        "operator.admin", "operator.read", "operator.write",
                    ],
                    "auth": {"token": self.gateway_token},
                },
            }
            ws.send(json.dumps(connect_req))

            # 3) 等待 connect response（跳过非 response 消息）
            while True:
                msg = json.loads(ws.recv())
                if msg.get("type") == "res" and msg.get("id") == connect_id:
                    if not msg.get("ok"):
                        error = msg.get("error", msg)
                        logger.error("compact: handshake rejected: %s", error)
                        result["error"] = f"handshake rejected: {error}"
                        ws.close()
                        return result
                    break

            logger.info("compact: connected to gateway via WebSocket RPC")

            # 4) 发送 sessions.compact
            compact_id = str(_uuid.uuid4())
            ws.send(json.dumps({
                "type": "req",
                "id": compact_id,
                "method": "sessions.compact",
                "params": {"key": session_key},
            }))
            logger.info("compact: sent sessions.compact for key=%s", session_key[:40])

            # 5) 等待 compact response
            while True:
                msg = json.loads(ws.recv())
                if msg.get("type") == "res" and msg.get("id") == compact_id:
                    payload = msg.get("payload", msg.get("result", {}))
                    logger.info("compact: response ok=%s payload=%s",
                                msg.get("ok"), json.dumps(payload, ensure_ascii=False)[:400])
                    if msg.get("ok"):
                        compacted = payload.get("compacted", False)
                        reason = payload.get("reason", "")
                        result["success"] = True
                        result["compacted"] = compacted
                        result["reason"] = reason
                        result["payload"] = payload
                        if not compacted:
                            logger.warning("compact: skipped — %s", reason or "unknown")
                    else:
                        error = msg.get("error", msg)
                        result["error"] = f"compact RPC failed: {error}"
                        logger.error("compact: RPC error: %s", error)
                    break

            ws.close()

        except Exception as e:
            logger.error("compact failed: %s", e)
            result["error"] = str(e)

        return result

    # ── 一键初始化 ────────────────────────────────────────────

    def wait_for_ov_ready(self, timeout: int = 120) -> bool:
        """等待 OV 服务就绪（端口监听 + HTTP 健康检查）。"""
        import requests as _req

        ov_port = getattr(self, "_ov_port", OPENVIKING_PORT)
        deadline = time.time() + timeout

        while time.time() < deadline:
            if ProcessManager.is_port_listening(ov_port):
                try:
                    resp = _req.get(f"http://127.0.0.1:{ov_port}/health", timeout=3)
                    if resp.status_code < 500:
                        logger.info("OV service ready on port %d", ov_port)
                        return True
                except Exception:
                    pass
            time.sleep(3)

        logger.warning("OV not ready after %ds", timeout)
        return False

    def full_setup(
        self,
        mode: str = "local",
        install_plugin: bool = True,
        plugin_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """完整的 profile 初始化流程。

        通过 OPENCLAW_STATE_DIR 隔离，使用 `openclaw openviking setup` 交互式配置。

        顺序:
          1. 创建 profile 目录
          2. 安装插件（OPENCLAW_STATE_DIR → 直接安装到 profile）
          3. 创建隔离 ov.conf
          4. 运行 `openclaw openviking setup` 交互式配置
          5. 复制 auth/providers、配置 models、gateway
          6. 启动 gateway
          7. (local 模式) 等待插件自动启动 OV
        """
        steps = {}
        steps["mode"] = mode

        # 0) 清理残留环境（应对上次 Ctrl+C 中断 teardown 未执行）
        if self.exists():
            logger.info("full_setup: cleaning up leftover profile '%s'", self.profile_name)
            self.full_teardown()
            time.sleep(1)

        # 1) Create profile
        steps["create"] = self.create()
        assert steps["create"], f"failed to create profile {self.profile_name}"

        # 2) Install plugin (OPENCLAW_STATE_DIR → 直接安装到 profile extensions/)
        if install_plugin:
            result = self.install_plugin(version=plugin_version)
            steps["plugin_installed"] = result["success"]
            if not result["success"]:
                steps["plugin_error"] = result.get("error") or result.get("output", "unknown error")
                return steps

        # 3) 创建隔离的 ov.conf（独立存储和端口）
        self.create_isolated_ov_conf()
        steps["ov_conf_created"] = True

        # 3b) Pre-configure plugins.allow so OpenClaw trusts the plugin commands
        pre_cfg = self.read_config()
        plugins_sec = pre_cfg.setdefault("plugins", {})
        allow_list = plugins_sec.get("allow", [])
        if PLUGIN_ID not in allow_list:
            allow_list.append(PLUGIN_ID)
        plugins_sec["allow"] = allow_list
        self.write_config(pre_cfg)

        # 4) 运行 `openclaw openviking setup` 交互式配置
        ov_port = getattr(self, "_ov_port", OPENVIKING_PORT)
        setup_result = self.run_interactive_setup(
            mode=mode,
            config_path=getattr(self, "_ov_conf", None),
            port=ov_port,
        )
        steps["interactive_setup"] = setup_result["success"]
        if not setup_result["success"]:
            setup_output = setup_result.get("output", "")
            if "unknown command" in setup_output or "readline was closed" in setup_output:
                logger.warning("interactive setup command unavailable, falling back to direct config")
                self.configure_plugin(
                    mode=mode,
                    ov_config_path=getattr(self, "_ov_conf", None),
                    ov_port=ov_port,
                )
                steps["interactive_setup"] = True
                steps["setup_fallback"] = "direct-config"
            else:
                steps["setup_error"] = setup_result.get("error", "unknown")
                logger.error("interactive setup failed: %s", setup_result)
                return steps
        logger.info("interactive setup completed: steps=%s", [s["name"] for s in setup_result.get("steps", [])])

        # 4b) 验证 setup 后 openclaw.json 是否包含完整的插件配置
        post_setup_cfg = self.read_config()
        plugin_cfg = post_setup_cfg.get("plugins", {}).get("entries", {}).get(PLUGIN_ID, {})
        plugin_config_inner = plugin_cfg.get("config", {})
        logger.info("post-setup openclaw.json plugin config (written by setup command): %s",
                     json.dumps(plugin_cfg, ensure_ascii=False))

        if not plugin_config_inner.get("mode"):
            logger.warning("setup wizard did not write plugin config (mode/configPath/port missing), "
                           "applying direct config as fallback")
            self.configure_plugin(
                mode=mode,
                ov_config_path=getattr(self, "_ov_conf", None),
                ov_port=ov_port,
            )
            steps["setup_fallback"] = "post-validation-fix"

        # 5) Remote 模式: 先启动 OV 服务
        if mode == "remote":
            from utils.env_manager import EnvManager
            if not ProcessManager.is_port_listening(OPENVIKING_PORT):
                logger.info("remote mode: starting OV server before gateway...")
                EnvManager.start_openviking_server()
            steps["ov_server_started"] = ProcessManager.wait_for_port(OPENVIKING_PORT, timeout=60)
            if not steps["ov_server_started"]:
                steps["ov_error"] = f"OV server failed to start on port {OPENVIKING_PORT}"
                return steps

        # 6) Copy auth from default profile
        self.configure_auth_from_default()
        steps["auth_copied"] = True

        # 7) Configure models
        self.configure_models()
        steps["models_configured"] = True

        # 8) Configure gateway (port, auth, controlUi)
        self.configure_gateway()
        steps["gateway_configured"] = True

        # 9) Start gateway
        steps["gateway_started"] = self.start_gateway()
        if not steps["gateway_started"]:
            return steps

        # 10) Local 模式: 等待插件自动启动 OV 服务
        if mode == "local":
            logger.info("local mode: waiting for OV plugin to auto-start OV server...")
            steps["ov_plugin_ready"] = self.wait_for_ov_ready(timeout=120)
            if steps["ov_plugin_ready"]:
                time.sleep(5)
                logger.info("OV plugin fully initialized")

        return steps

    def full_teardown(self) -> Dict[str, Any]:
        """完整的 profile 清理。"""
        steps = {}
        steps["gateway_stopped"] = self.stop_gateway()
        steps["destroyed"] = self.destroy()
        return steps
