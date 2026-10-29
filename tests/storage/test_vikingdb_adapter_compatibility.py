# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exact legacy adapter payloads remain independent of collection metadata."""

from unittest.mock import AsyncMock

import pytest

from openviking.storage.expr import And, Eq, RawDSL
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from tests.storage.test_opengauss_retrieval_upper import make_backend


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vikingdb", "volcengine", "opengauss"])
@pytest.mark.parametrize("fields", [None, ["content"], ["title", "body"]])
@pytest.mark.parametrize("search_mode", [None, "bm25", "custom"])
async def test_keyword_payload_and_metadata_independence(mode, fields, search_mode):
    backend, adapter = make_backend(mode)
    backend.get_collection_meta = AsyncMock(side_effect=RuntimeError("metadata unavailable"))
    original = {"op": "must", "field": "status", "conds": ["ready"]}
    result = await backend.search_by_keywords(
        keywords=["one"], query="query", mode=search_mode, fields=fields,
        limit=7, offset=3, filter=original, output_fields=["id", "uri"],
    )
    assert result == [{"id": "one", "_score": 1.5}]
    adapter.search_by_keywords.assert_called_once_with(
        keywords=["one"], query="query", mode=search_mode, fields=fields,
        limit=7, offset=3, filter=And([Eq("account_id", "acct"), RawDSL(original)]),
        output_fields=["id", "uri"],
    )
    backend.get_collection_meta.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "vikingdb", "volcengine", "opengauss"])
async def test_content_switch_uses_live_adapter_flag_without_borrowing_or_metadata(mode):
    backend, adapter = make_backend(mode)
    backend.get_collection_meta = AsyncMock(side_effect=AssertionError("metadata forbidden"))
    backend._retired = True
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade.get_account_backend = AsyncMock(return_value=backend)
    for enabled in (False, True):
        adapter.USE_CONTENT_FIELD = enabled
        assert await facade.account_uses_content_field("acct") is enabled
    assert facade.get_account_backend.await_count == 2
    facade.get_account_backend.assert_awaited_with("acct")
    backend.get_collection_meta.assert_not_awaited()
