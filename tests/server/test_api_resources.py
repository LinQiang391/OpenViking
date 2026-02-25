# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for resource management endpoints."""

import httpx


async def test_add_resource_success(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource",
            "wait": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]
    assert body["result"]["root_uri"].startswith("viking://")


async def test_add_resource_with_wait(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]


async def test_add_resource_with_trace_wait(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "trace resource",
            "wait": True,
            "trace": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    trace_summary = body["result"]["trace"]["summary"]
    assert trace_summary["operation"] == "resources.add_resource"
    semantic = trace_summary["semantic_nodes"]
    assert semantic["total_nodes"] is None or semantic["done_nodes"] == semantic["total_nodes"]
    assert semantic["pending_nodes"] in (None, 0)
    assert semantic["in_progress_nodes"] in (None, 0)


async def test_add_resource_file_not_found(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "/nonexistent/file.txt", "reason": "test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "errors" in body["result"] and len(body["result"]["errors"]) > 0


async def test_add_resource_with_target(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "target": "viking://resources/custom/",
            "reason": "test resource",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "custom" in body["result"]["root_uri"]


async def test_wait_processed_empty_queue(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 30.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_wait_processed_after_add(client: httpx.AsyncClient, sample_markdown_file):
    await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test"},
    )
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 60.0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
