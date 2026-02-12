# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""MCP (Model Context Protocol) router for OpenViking HTTP Server.

Exposes OpenViking server APIs via MCP protocol at /mcp endpoint.
Implements MCP 2024-11-05 protocol over HTTP.

Maps to actual OpenViking service APIs:
- search.find() - Semantic search without session
- search.search() - Semantic search with session context
- fs.ls() - List directory contents
- fs.stat() - Get resource metadata
- fs.grep() - Content search with pattern
- fs.glob() - File pattern matching
- resources.add_resource() - Add resource to database
- sessions.session() / sessions.delete() - Session management
"""

import json
from typing import Any, Callable, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openviking.server.dependencies import get_service
from openviking.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["mcp"])

# MCP Tool definitions - mapping to actual server service APIs
TOOLS: Dict[str, Dict[str, Any]] = {
    "search_find": {
        "description": "Semantic search without session context. Returns relevant documents with scores.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string."},
                "target_uri": {
                    "type": "string",
                    "description": "Target directory URI to scope the search (e.g., 'viking://resources/docs/').",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10).",
                    "default": 10,
                },
                "score_threshold": {
                    "type": "number",
                    "description": "Minimum relevance score threshold (0.0-1.0).",
                },
            },
            "required": ["query"],
        },
    },
    "search_search": {
        "description": "Semantic search with optional session context for personalized results.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string."},
                "target_uri": {
                    "type": "string",
                    "description": "Target directory URI to scope the search.",
                    "default": "",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID for context-aware search.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10).",
                    "default": 10,
                },
                "score_threshold": {
                    "type": "number",
                    "description": "Minimum relevance score threshold (0.0-1.0).",
                },
            },
            "required": ["query"],
        },
    },
    "fs_ls": {
        "description": "List directory contents at a given Viking URI.",
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Viking URI to list (e.g., 'viking://resources/').",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List subdirectories recursively.",
                    "default": False,
                },
            },
            "required": ["uri"],
        },
    },
    "fs_stat": {
        "description": "Get metadata for a resource at a Viking URI.",
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Viking URI to stat (e.g., 'viking://resources/doc.txt').",
                },
            },
            "required": ["uri"],
        },
    },
    "fs_grep": {
        "description": "Search file contents with a regex pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Viking URI to search within.",
                },
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive matching.",
                    "default": False,
                },
            },
            "required": ["uri", "pattern"],
        },
    },
    "fs_glob": {
        "description": "Find files matching a glob pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g., '*.py', '**/*.md').",
                },
                "uri": {
                    "type": "string",
                    "description": "Base URI to search from (default: 'viking://').",
                    "default": "viking://",
                },
            },
            "required": ["pattern"],
        },
    },
    "resources_add": {
        "description": "Add a document, file, directory, or URL to OpenViking.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to local file/directory or URL to add.",
                },
                "target": {
                    "type": "string",
                    "description": "Optional target URI in OpenViking.",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for processing to complete.",
                    "default": False,
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds when waiting.",
                },
            },
            "required": ["path"],
        },
    },
    "session_create": {
        "description": "Create a new session for context-aware operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Optional user identifier.",
                },
            },
        },
    },
    "session_delete": {
        "description": "Delete a session.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to delete."},
            },
            "required": ["session_id"],
        },
    },
}

# MCP Resource definitions
RESOURCES: Dict[str, Dict[str, Any]] = {
    "openviking://status": {
        "name": "Server Status",
        "mimeType": "application/json",
    },
}


# ========== Tool Handlers ==========


async def handle_search_find(args: Dict[str, Any]) -> str:
    """Handle search_find tool - calls service.search.find()"""
    service = get_service()

    result = await service.search.find(
        query=args.get("query", ""),
        target_uri=args.get("target_uri", ""),
        limit=args.get("limit", 10),
        score_threshold=args.get("score_threshold"),
    )

    if hasattr(result, "to_dict"):
        result = result.to_dict()

    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_search_search(args: Dict[str, Any]) -> str:
    """Handle search_search tool - calls service.search.search()"""
    service = get_service()

    session = None
    session_id = args.get("session_id")
    if session_id:
        session = service.sessions.session(session_id)
        session.load()

    result = await service.search.search(
        query=args.get("query", ""),
        target_uri=args.get("target_uri", ""),
        session=session,
        limit=args.get("limit", 10),
        score_threshold=args.get("score_threshold"),
    )

    if hasattr(result, "to_dict"):
        result = result.to_dict()

    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_fs_ls(args: Dict[str, Any]) -> str:
    """Handle fs_ls tool - calls service.fs.ls()"""
    service = get_service()

    result = await service.fs.ls(
        uri=args.get("uri", ""),
        recursive=args.get("recursive", False),
        simple=False,
    )

    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_fs_stat(args: Dict[str, Any]) -> str:
    """Handle fs_stat tool - calls service.fs.stat()"""
    service = get_service()

    result = await service.fs.stat(args.get("uri", ""))
    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_fs_grep(args: Dict[str, Any]) -> str:
    """Handle fs_grep tool - calls service.fs.grep()"""
    service = get_service()

    result = await service.fs.grep(
        uri=args.get("uri", ""),
        pattern=args.get("pattern", ""),
        case_insensitive=args.get("case_insensitive", False),
    )

    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_fs_glob(args: Dict[str, Any]) -> str:
    """Handle fs_glob tool - calls service.fs.glob()"""
    service = get_service()

    result = await service.fs.glob(
        pattern=args.get("pattern", ""),
        uri=args.get("uri", "viking://"),
    )

    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_resources_add(args: Dict[str, Any]) -> str:
    """Handle resources_add tool - calls service.resources.add_resource()"""
    service = get_service()

    result = await service.resources.add_resource(
        path=args.get("path", ""),
        target=args.get("target"),
        wait=args.get("wait", False),
        timeout=args.get("timeout"),
    )

    return json.dumps(result, indent=2, ensure_ascii=False)


async def handle_session_create(args: Dict[str, Any]) -> str:
    """Handle session_create tool - calls service.sessions.session()"""
    service = get_service()

    session = service.sessions.session()
    return json.dumps(
        {"session_id": session.session_id, "user": session.user},
        indent=2,
        ensure_ascii=False,
    )


async def handle_session_delete(args: Dict[str, Any]) -> str:
    """Handle session_delete tool - calls service.sessions.delete()"""
    service = get_service()

    session_id = args.get("session_id", "")
    await service.sessions.delete(session_id)
    return json.dumps({"deleted": True, "session_id": session_id}, indent=2)


# Tool handlers mapping
TOOL_HANDLERS: Dict[str, Callable] = {
    "search_find": handle_search_find,
    "search_search": handle_search_search,
    "fs_ls": handle_fs_ls,
    "fs_stat": handle_fs_stat,
    "fs_grep": handle_fs_grep,
    "fs_glob": handle_fs_glob,
    "resources_add": handle_resources_add,
    "session_create": handle_session_create,
    "session_delete": handle_session_delete,
}


# ========== Resource Handlers ==========


def handle_resource_status() -> str:
    """Handle status resource read."""
    service = get_service()
    # Ensure initialized is always a boolean for JSON serialization
    initialized = getattr(service, "_initialized", None)
    if not isinstance(initialized, bool):
        initialized = bool(initialized) if initialized is not None else True
    info = {
        "initialized": initialized,
        "status": "running",
    }
    return json.dumps(info, indent=2)


RESOURCE_HANDLERS: Dict[str, Callable] = {
    "openviking://status": handle_resource_status,
}


# ========== MCP HTTP Endpoints ==========


@router.get("/mcp")
async def mcp_get():
    """MCP HTTP endpoint for GET requests - returns server info."""
    return JSONResponse(
        content={
            "name": "openviking",
            "version": "0.1.0",
            "protocol": "mcp",
            "endpoint": "/mcp",
        }
    )


@router.post("/mcp")
async def mcp_post(request: Request):
    """MCP HTTP endpoint for POST requests - handles JSON-RPC."""
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
            },
        )

    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id")

    try:
        if method == "initialize":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                        },
                        "serverInfo": {
                            "name": "openviking",
                            "version": "0.1.0",
                        },
                    },
                }
            )

        elif method == "ping":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {},
                }
            )

        elif method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": tool_def["description"],
                    "inputSchema": tool_def["parameters"],
                }
                for name, tool_def in TOOLS.items()
            ]
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": tools},
                }
            )

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name not in TOOL_HANDLERS:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
                    }
                )

            try:
                handler = TOOL_HANDLERS[tool_name]
                result = await handler(tool_args)

                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": result}],
                        },
                    }
                )
            except Exception as e:
                logger.exception(f"Tool execution error: {tool_name}")
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": f"Tool error: {str(e)}"},
                    }
                )

        elif method == "resources/list":
            resources = [
                {
                    "uri": uri,
                    "name": res_def.get("name", uri),
                    "mimeType": res_def.get("mimeType", "text/plain"),
                }
                for uri, res_def in RESOURCES.items()
            ]
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"resources": resources},
                }
            )

        elif method == "resources/read":
            uri = params.get("uri", "")

            if uri not in RESOURCE_HANDLERS:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": f"Resource not found: {uri}"},
                    }
                )

            try:
                handler = RESOURCE_HANDLERS[uri]
                result = handler()

                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "contents": [
                                {
                                    "uri": uri,
                                    "mimeType": RESOURCES.get(uri, {}).get(
                                        "mimeType", "text/plain"
                                    ),
                                    "text": result,
                                }
                            ],
                        },
                    }
                )
            except Exception as e:
                logger.exception(f"Resource read error: {uri}")
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": f"Resource error: {str(e)}"},
                    }
                )

        else:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )

    except Exception as e:
        logger.exception("MCP request error")
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(e)},
            }
        )
