# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for MCP router."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def mock_service():
    """Create a mock service for MCP router tests."""
    service = MagicMock()
    service._initialized = True

    # Search methods
    search_result = MagicMock()
    search_result.to_dict.return_value = {
        "resources": [{"uri": "viking://test", "score": 0.95}]
    }
    service.search.find = AsyncMock(return_value=search_result)
    service.search.search = AsyncMock(return_value=search_result)

    # Filesystem methods
    service.fs.ls = AsyncMock(return_value={"entries": [{"name": "test"}]})
    service.fs.stat = AsyncMock(return_value={"uri": "viking://test", "size": 100})
    service.fs.grep = AsyncMock(return_value={"matches": []})
    service.fs.glob = AsyncMock(return_value={"matches": []})

    # Resources methods
    service.resources.add_resource = AsyncMock(
        return_value={"root_uri": "viking://resources/new"}
    )

    # Session methods
    session_mock = MagicMock(session_id="test-session", user=None)
    service.sessions.session = MagicMock(return_value=session_mock)
    service.sessions.delete = AsyncMock(return_value=None)

    return service


@pytest.fixture
def client(mock_service):
    """Create a test client with mocked service."""
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    from openviking.server.routers.mcp import router as mcp_router
    from openviking.server import dependencies

    # Mock the service
    dependencies._service = mock_service
    dependencies.get_service = lambda: mock_service

    app = FastAPI()
    app.include_router(mcp_router)

    return app


@pytest.mark.asyncio
async def test_mcp_get_endpoint(client):
    """Test GET /mcp endpoint returns server info."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/mcp")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "openviking"
    assert data["version"] == "0.1.0"
    assert data["protocol"] == "mcp"


@pytest.mark.asyncio
async def test_mcp_initialize(client):
    """Test MCP initialize method."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"}
            }
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert data["result"]["serverInfo"]["name"] == "openviking"
    assert "tools" in data["result"]["capabilities"]
    assert "resources" in data["result"]["capabilities"]


@pytest.mark.asyncio
async def test_mcp_ping(client):
    """Test MCP ping method."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "ping"
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 2
    assert data["result"] == {}


@pytest.mark.asyncio
async def test_mcp_tools_list(client):
    """Test MCP tools/list method."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list"
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 3
    tools = data["result"]["tools"]
    assert len(tools) == 9

    tool_names = {tool["name"] for tool in tools}
    expected_tools = {
        "search_find", "search_search", "fs_ls", "fs_stat",
        "fs_grep", "fs_glob", "resources_add", "session_create", "session_delete"
    }
    assert tool_names == expected_tools

    # Verify tool structure
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


@pytest.mark.asyncio
async def test_mcp_resources_list(client):
    """Test MCP resources/list method."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/list"
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 4
    resources = data["result"]["resources"]
    assert len(resources) == 1
    assert resources[0]["uri"] == "openviking://status"


@pytest.mark.asyncio
async def test_mcp_resources_read(client):
    """Test MCP resources/read method."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "resources/read",
            "params": {"uri": "openviking://status"}
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 5
    contents = data["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == "openviking://status"

    # Verify content is valid JSON
    content_text = contents[0]["text"]
    content_data = json.loads(content_text)
    assert "status" in content_data
    assert content_data["status"] == "running"
    assert "initialized" in content_data
    assert content_data["initialized"] is True


@pytest.mark.asyncio
async def test_mcp_tools_call_search_find(client, mock_service):
    """Test MCP tools/call method for search_find."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "search_find",
                "arguments": {"query": "test", "limit": 5}
            }
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 6
    content = data["result"]["content"][0]
    assert content["type"] == "text"

    # Verify the tool was called correctly
    mock_service.search.find.assert_called_once_with(
        query="test",
        target_uri="",
        limit=5,
        score_threshold=None
    )


@pytest.mark.asyncio
async def test_mcp_tools_call_fs_ls(client, mock_service):
    """Test MCP tools/call method for fs_ls."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "fs_ls",
                "arguments": {"uri": "viking://resources/", "recursive": True}
            }
        })

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 7

    # Verify the tool was called correctly
    mock_service.fs.ls.assert_called_once_with(
        uri="viking://resources/",
        recursive=True,
        simple=False
    )


@pytest.mark.asyncio
async def test_mcp_tools_call_unknown_tool(client):
    """Test MCP tools/call with unknown tool returns error."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "unknown_tool",
                "arguments": {}
            }
        })

    assert response.status_code == 200  # MCP returns 200 even for errors
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32601
    assert "unknown_tool" in data["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_resources_read_unknown(client):
    """Test MCP resources/read with unknown resource returns error."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "resources/read",
            "params": {"uri": "openviking://unknown"}
        })

    assert response.status_code == 200  # MCP returns 200 even for errors
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32602
    assert "unknown" in data["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_invalid_json(client):
    """Test MCP endpoint with invalid JSON returns parse error."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/mcp",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32700  # Parse error


@pytest.mark.asyncio
async def test_mcp_unknown_method(client):
    """Test MCP with unknown method returns error."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=client)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "unknown/method"
        })

    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32601  # Method not found
