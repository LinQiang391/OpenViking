"""
OpenViking 配置管理：创建、验证、修改 ov.conf
"""

import copy
import json
import logging
import os
from typing import Any, Dict, List, Optional

from config.settings import OPENVIKING_CONF_CANDIDATES, OPENVIKING_HOME, PROJECT_ROOT

logger = logging.getLogger(__name__)


class ConfigManager:
    """OpenViking 服务配置管理器"""

    REQUIRED_SECTIONS = ["server", "storage", "embedding"]
    OPTIONAL_SECTIONS = ["vlm", "rerank", "parsers", "encryption", "log", "memory"]

    # ── 读取 & 写入 ──────────────────────────────────────────

    @staticmethod
    def find_config() -> Optional[str]:
        for path in OPENVIKING_CONF_CANDIDATES:
            if os.path.isfile(path):
                return path
        return None

    @staticmethod
    def read_config(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
        path = path or ConfigManager.find_config()
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.error("failed to read config %s: %s", path, exc)
            return None

    @staticmethod
    def write_config(config: Dict[str, Any], path: Optional[str] = None) -> str:
        path = path or os.path.join(OPENVIKING_HOME, "ov.conf")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info("wrote ov.conf to %s", path)
        return path

    # ── 验证 ──────────────────────────────────────────────────

    @staticmethod
    def validate_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """验证配置的完整性和正确性"""
        result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "sections_found": [],
            "sections_missing": [],
        }

        if config is None:
            config = ConfigManager.read_config()
        if config is None:
            result["valid"] = False
            result["errors"].append("无法读取配置文件")
            return result

        # Check required sections
        for section in ConfigManager.REQUIRED_SECTIONS:
            if section in config:
                result["sections_found"].append(section)
            else:
                result["sections_missing"].append(section)
                result["errors"].append(f"缺少必要配置段: {section}")
                result["valid"] = False

        # Check optional sections
        for section in ConfigManager.OPTIONAL_SECTIONS:
            if section in config:
                result["sections_found"].append(section)

        # Validate server section
        server = config.get("server", {})
        if not server.get("port"):
            result["errors"].append("server.port 未配置")
            result["valid"] = False
        elif not (1 <= server["port"] <= 65535):
            result["errors"].append(f"server.port 无效: {server['port']}")
            result["valid"] = False

        if not server.get("host"):
            result["warnings"].append("server.host 未配置，将使用默认值")

        # Validate storage section
        storage = config.get("storage", {})
        workspace = storage.get("workspace")
        if not workspace:
            result["errors"].append("storage.workspace 未配置")
            result["valid"] = False

        # Validate embedding section
        embedding = config.get("embedding", {})
        dense = embedding.get("dense", {})
        if not dense.get("model"):
            result["errors"].append("embedding.dense.model 未配置")
            result["valid"] = False
        if not dense.get("api_key") or dense.get("api_key") == "{your-api-key}":
            result["warnings"].append("embedding.dense.api_key 未配置或为占位符")
        if not dense.get("api_base"):
            result["warnings"].append("embedding.dense.api_base 未配置")

        # Validate VLM if present
        vlm = config.get("vlm", {})
        if vlm:
            if not vlm.get("model"):
                result["warnings"].append("vlm.model 未配置")
            if not vlm.get("api_key") or vlm.get("api_key") == "{your-api-key}":
                result["warnings"].append("vlm.api_key 未配置或为占位符")

        return result

    # ── 配置项操作 ────────────────────────────────────────────

    @staticmethod
    def get_server_info(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        config = config or ConfigManager.read_config()
        if not config:
            return {}
        server = config.get("server", {})
        return {
            "host": server.get("host", "127.0.0.1"),
            "port": server.get("port", 1933),
            "cors_origins": server.get("cors_origins", []),
            "root_api_key": "***" if server.get("root_api_key") else None,
        }

    @staticmethod
    def get_storage_info(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        config = config or ConfigManager.read_config()
        if not config:
            return {}
        storage = config.get("storage", {})
        workspace = storage.get("workspace", "")
        return {
            "workspace": workspace,
            "workspace_exists": os.path.isdir(os.path.expanduser(workspace)) if workspace else False,
            "vectordb_backend": storage.get("vectordb", {}).get("backend", "local"),
            "agfs_backend": storage.get("agfs", {}).get("backend", "local"),
        }

    @staticmethod
    def get_embedding_info(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        config = config or ConfigManager.read_config()
        if not config:
            return {}
        embedding = config.get("embedding", {})
        dense = embedding.get("dense", {})
        return {
            "provider": dense.get("provider", "unknown"),
            "model": dense.get("model", "unknown"),
            "api_base": dense.get("api_base", ""),
            "dimension": dense.get("dimension"),
            "has_api_key": bool(dense.get("api_key") and dense.get("api_key") != "{your-api-key}"),
        }

    @staticmethod
    def get_memory_version(config: Optional[Dict[str, Any]] = None) -> str:
        config = config or ConfigManager.read_config()
        if not config:
            return "unknown"
        return config.get("memory", {}).get("version", "v1")

    # ── 修改配置 ──────────────────────────────────────────────

    @staticmethod
    def update_server_port(port: int, config_path: Optional[str] = None) -> Dict[str, Any]:
        config_path = config_path or ConfigManager.find_config()
        config = ConfigManager.read_config(config_path)
        if not config:
            return {"error": "config not found"}
        config.setdefault("server", {})["port"] = port
        ConfigManager.write_config(config, config_path)
        return config

    @staticmethod
    def update_server_host(host: str, config_path: Optional[str] = None) -> Dict[str, Any]:
        config_path = config_path or ConfigManager.find_config()
        config = ConfigManager.read_config(config_path)
        if not config:
            return {"error": "config not found"}
        config.setdefault("server", {})["host"] = host
        ConfigManager.write_config(config, config_path)
        return config

    @staticmethod
    def generate_from_test_config(
        port: int = 1933,
        data_dir: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """从 test_config.json 的 ov_conf 段独立生成完整 ov.conf。

        不依赖任何模板文件。port 和 data_dir (storage.workspace) 由调用方
        按隔离 profile 的需求覆盖。其余配置项全部取自 test_config.json。
        """
        from config.settings import OV_CONF_TEMPLATE

        config = copy.deepcopy(OV_CONF_TEMPLATE)
        if not config:
            raise ValueError("test_config.json 中缺少 ov_conf 段，无法生成 ov.conf")

        config.setdefault("server", {})["port"] = port
        if data_dir:
            config.setdefault("storage", {})["workspace"] = data_dir

        output = output_path or os.path.join(OPENVIKING_HOME, "ov.conf")
        ConfigManager.write_config(config, output)
        return output

    @staticmethod
    def create_test_config(
        port: int = 1933,
        host: str = "0.0.0.0",
        workspace: Optional[str] = None,
        base_config_path: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """基于已有配置创建测试用的临时配置（兼容旧模板方式）。"""
        if base_config_path:
            config = ConfigManager.read_config(base_config_path) or {}
        else:
            config = ConfigManager.read_config() or {}

        config = copy.deepcopy(config)
        config.setdefault("server", {})["port"] = port
        config["server"]["host"] = host
        if workspace:
            config.setdefault("storage", {})["workspace"] = workspace

        output = output_path or os.path.join(OPENVIKING_HOME, "ov.conf.test")
        ConfigManager.write_config(config, output)
        return output

    @staticmethod
    def backup_config(config_path: Optional[str] = None) -> Optional[str]:
        """备份配置文件"""
        config_path = config_path or ConfigManager.find_config()
        if not config_path or not os.path.isfile(config_path):
            return None
        backup_path = config_path + ".backup"
        import shutil
        shutil.copy2(config_path, backup_path)
        logger.info("config backed up to %s", backup_path)
        return backup_path

    @staticmethod
    def restore_config(backup_path: str, target_path: Optional[str] = None) -> bool:
        """从备份恢复配置"""
        if not os.path.isfile(backup_path):
            return False
        target = target_path or backup_path.replace(".backup", "")
        import shutil
        shutil.copy2(backup_path, target)
        logger.info("config restored from %s to %s", backup_path, target)
        return True

    @staticmethod
    def full_config_report() -> Dict[str, Any]:
        """生成完整的配置报告"""
        config = ConfigManager.read_config()
        return {
            "config_path": ConfigManager.find_config(),
            "config_exists": config is not None,
            "validation": ConfigManager.validate_config(config),
            "server": ConfigManager.get_server_info(config),
            "storage": ConfigManager.get_storage_info(config),
            "embedding": ConfigManager.get_embedding_info(config),
            "memory_version": ConfigManager.get_memory_version(config),
        }
