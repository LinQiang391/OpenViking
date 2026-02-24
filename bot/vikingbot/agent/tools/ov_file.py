"""OpenViking file system tools: read, write, list, search resources."""

from typing import Any, Optional
from pathlib import Path
from loguru import logger

from vikingbot.agent.tools.base import Tool
from vikingbot.openviking_mount.ov_server import VikingClient


class VikingReadTool(Tool):
    """Tool to read content from Viking resources."""

    @property
    def name(self) -> str:
        return "openviking_read"

    @property
    def description(self) -> str:
        return "Read content from OpenViking resources at different levels (abstract, overview, or full content)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The Viking URI to read from (e.g., viking://resources/path/)",
                },
                "level": {
                    "type": "string",
                    "description": "Reading level: 'abstract' (L0 summary), 'overview' (L1 overview), or 'read' (L2 full content)",
                    "enum": ["abstract", "overview", "read"],
                    "default": "abstract",
                },
            },
            "required": ["uri"],
        }

    async def execute(self, uri: str, level: str = "abstract", **kwargs: Any) -> str:
        try:
            from vikingbot.config.loader import load_config

            config = load_config()
            viking_url = config.tools.openviking.server_url
            client = VikingClient(viking_url)
            content = client.read_content(uri, level=level)
            return content
        except Exception as e:
            return f"Error reading from Viking: {str(e)}"


class VikingListTool(Tool):
    """Tool to list Viking resources."""

    @property
    def name(self) -> str:
        return "openviking_list"

    @property
    def description(self) -> str:
        return "List resources in a OpenViking path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The Viking path to list (e.g., /bot_test/dutao/ or viking://resources/bot_test/dutao/)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively",
                    "default": False,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, recursive: bool = False, **kwargs: Any) -> str:
        try:
            from vikingbot.config.loader import load_config

            config = load_config()
            viking_url = config.tools.openviking.server_url
            client = VikingClient(viking_url)
            if path.startswith("viking://"):
                viking_path = path.replace("viking://resources", "")
            else:
                viking_path = path

            entries = client.list_resources(path=viking_path, recursive=recursive)

            if not entries:
                return f"No resources found at {path}"

            result = []
            for entry in entries:
                type_str = "📁 " if entry.get("isDir", False) else "📄 "
                result.append(f"{type_str}{entry.get('name', 'unknown')}")

            return "\n".join(result)
        except Exception as e:
            return f"Error listing Viking resources: {str(e)}"


class VikingSearchTool(Tool):
    """Tool to search Viking resources."""

    @property
    def name(self) -> str:
        return "openviking_search"

    @property
    def description(self) -> str:
        return "Search for resources in OpenViking using a query."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "target_uri": {
                    "type": "string",
                    "description": "Optional target URI to limit search scope (e.g., /bot_test/dutao/)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, target_uri: Optional[str] = None, **kwargs: Any) -> str:
        try:
            from vikingbot.config.loader import load_config

            config = load_config()
            viking_url = config.tools.openviking.server_url
            client = VikingClient(viking_url)
            logger.warning(f"Viking query: {query}")
            results = client.search(query, target_uri=target_uri)

            if not results:
                return f"No results found for query: {query}"
            if isinstance(results, list):
                result_strs = []
                for i, result in enumerate(results, 1):
                    result_strs.append(f"{i}. {str(result)}")
                return "\n".join(result_strs)
            else:
                return str(results)
        except Exception as e:
            return f"Error searching Viking: {str(e)}"


class VikingAddResourceTool(Tool):
    """Tool to add a resource to Viking."""

    @property
    def name(self) -> str:
        return "openviking_add_resource"

    @property
    def description(self) -> str:
        return "Add a local file as a resource to OpenViking."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "local_path": {"type": "string", "description": "Path to the local file to add"},
                "description": {"type": "string", "description": "Description of the resource"},
                "target_path": {
                    "type": "string",
                    "description": "Target path in Viking (e.g., /bot_test/dutao/docs/)",
                    "default": "",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Whether to wait for processing to complete",
                    "default": False,
                },
            },
            "required": ["local_path", "description"],
        }

    async def execute(
        self,
        local_path: str,
        description: str,
        target_path: str = "",
        wait: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            path = Path(local_path).expanduser().resolve()
            if not path.exists():
                return f"Error: File not found: {local_path}"
            if not path.is_file():
                return f"Error: Not a file: {local_path}"

            from vikingbot.config.loader import load_config

            config = load_config()
            viking_url = config.tools.openviking.server_url
            client = VikingClient(viking_url)
            result = client.add_resource(
                str(path), description, target_path=target_path or None, wait=wait
            )

            if result:
                root_uri = result.get("result", {}).get("root_uri", "unknown")
                return f"Successfully added resource: {root_uri}"
            else:
                return "Failed to add resource"
        except Exception as e:
            return f"Error adding resource to Viking: {str(e)}"


class VikingGrepTool(Tool):
    """Tool to search Viking resources using regex patterns."""

    @property
    def name(self) -> str:
        return "openviking_grep"

    @property
    def description(self) -> str:
        return "Search Viking resources using regex patterns (like grep)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The Viking URI to search within (e.g., viking://resources/path/, /bot_test/dutao/)",
                },
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search",
                    "default": False,
                },
            },
            "required": ["uri", "pattern"],
        }

    async def execute(
        self, uri: str, pattern: str, case_insensitive: bool = False, **kwargs: Any
    ) -> str:
        try:
            from vikingbot.config.loader import load_config

            config = load_config()
            viking_url = config.tools.openviking.server_url
            client = VikingClient(viking_url)
            result = client.grep(uri, pattern, case_insensitive=case_insensitive)

            if isinstance(result, dict):
                matches = result.get("result", {}).get("matches", [])
                count = result.get("result", {}).get("count", 0)
            else:
                matches = getattr(result, "matches", [])
                count = getattr(result, "count", 0)

            if not matches:
                return f"No matches found for pattern: {pattern}"

            result_lines = [f"Found {count} match{'es' if count != 1 else ''}:"]
            for match in matches:
                if isinstance(match, dict):
                    match_uri = match.get("uri", "unknown")
                    line = match.get("line", "?")
                    content = match.get("content", "")
                else:
                    match_uri = getattr(match, "uri", "unknown")
                    line = getattr(match, "line", "?")
                    content = getattr(match, "content", "")
                result_lines.append(f"\n📄 {match_uri}:{line}")
                result_lines.append(f"   {content}")

            return "\n".join(result_lines)
        except Exception as e:
            return f"Error searching Viking with grep: {str(e)}"


class VikingGlobTool(Tool):
    """Tool to find Viking resources using glob patterns."""

    @property
    def name(self) -> str:
        return "openviking_glob"

    @property
    def description(self) -> str:
        return "Find Viking resources using glob patterns (like **/*.md, *.py)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g., **/*.md, *.py, src/**/*.js)",
                },
                "uri": {
                    "type": "string",
                    "description": "Optional starting URI (e.g., viking://resources/path/, /bot_test/dutao/)",
                    "default": "",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, uri: str = "", **kwargs: Any) -> str:
        try:
            from vikingbot.config.loader import load_config

            config = load_config()
            viking_url = config.tools.openviking.server_url
            client = VikingClient(viking_url)
            result = client.glob(pattern, uri=uri or None)

            if isinstance(result, dict):
                matches = result.get("result", {}).get("matches", [])
                count = result.get("result", {}).get("count", 0)
            else:
                matches = getattr(result, "matches", [])
                count = getattr(result, "count", 0)

            if not matches:
                return f"No files found for pattern: {pattern}"

            result_lines = [f"Found {count} file{'s' if count != 1 else ''}:"]
            for match_uri in matches:
                if isinstance(match_uri, dict):
                    match_uri = match_uri.get("uri", str(match_uri))
                result_lines.append(f"📄 {match_uri}")

            return "\n".join(result_lines)
        except Exception as e:
            return f"Error searching Viking with glob: {str(e)}"
