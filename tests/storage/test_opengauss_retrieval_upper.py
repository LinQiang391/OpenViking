# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""New adapters must pass through the existing public storage contract unchanged."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq
from openviking.storage.viking_vector_index_backend import (
    VIKINGDB_CONTENT_MAX_SIZE,
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

MODES = ["local", "volcengine", "vikingdb", "opengauss"]


def make_backend(mode, enabled=True):
    adapter = SimpleNamespace(
        mode=mode, USE_CONTENT_FIELD=enabled,
        get_collection=lambda: SimpleNamespace(get_meta_data=lambda **kwargs: {
            "Fields": [{"FieldName": "id"}, {"FieldName": "content", "FieldType": "text"},
                       {"FieldName": "account_id"}],
            "FullText": [{"Field": "content"}],
        }),
        collection_exists=lambda: True,
        search_by_keywords=Mock(return_value=[{"id": "one", "_score": 1.5}]),
    )
    backend = _SingleAccountBackend(
        VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acct", shared_adapter=adapter,
    )
    return backend, adapter


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("value", ["正文" * VIKINGDB_CONTENT_MAX_SIZE, b"x" * (VIKINGDB_CONTENT_MAX_SIZE + 1)])
def test_all_backends_share_legacy_content_cap_and_do_not_mutate_input(mode, enabled, value):
    backend, _ = make_backend(mode, enabled)
    for prepare in (backend._prepare_upsert_payload, backend._prepare_update_payload):
        source = {"id": "one", "content": value}
        result = prepare(source)
        if enabled:
            assert result["content"] == value[:VIKINGDB_CONTENT_MAX_SIZE]
        else:
            assert "content" not in result
        assert source == {"id": "one", "content": value}


@pytest.mark.parametrize("mode", MODES)
def test_full_write_defaults_and_partial_write_omissions_stay_unchanged(mode):
    backend, _ = make_backend(mode)
    assert backend._prepare_upsert_payload({"id": "one"}) == {"id": "one", "content": ""}
    assert backend._prepare_update_payload({"id": "one"}) == {"id": "one"}
    assert backend._prepare_update_payload({"content": ""}) == {"content": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("options", [{}, {"mode": "bm25", "fields": ["content"]}])
async def test_keyword_options_and_account_scope_are_forwarded_without_new_public_policy(mode, options):
    backend, adapter = make_backend(mode)
    assert await backend.search_by_keywords(query="query", filter=Eq("level", 2), **options) == [
        {"id": "one", "_score": 1.5},
    ]
    kwargs = adapter.search_by_keywords.call_args.kwargs
    assert kwargs["mode"] == options.get("mode")
    assert kwargs["fields"] == options.get("fields")
    assert kwargs["filter"] == And([Eq("account_id", "acct"), Eq("level", 2)])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_tenant_keyword_search_keeps_exact_scope_and_account_selection(mode):
    ctx = RequestContext(user=UserIdentifier("acct", "user"), role=Role.USER)
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._acl_enabled = AsyncMock(return_value=True)
    facade._build_scope_filter = Mock(return_value=Eq("uri", "scoped"))
    backend, adapter = make_backend(mode)
    facade._get_backend_for_context = AsyncMock(return_value=backend)
    await facade.search_by_keywords_in_tenant(
        ctx, "query", target_directories=["viking://resources/repo"],
        extra_filter={"tag": "value"}, level=[2], limit=7, offset=3,
    )
    scope = facade._build_scope_filter.call_args.kwargs
    assert scope["acl_enabled"] is True
    assert scope["level"] == [2]
    assert scope["target_directories"] == ["viking://resources/repo"]
    assert scope["extra_filter"] == {"tag": "value"}
    kwargs = adapter.search_by_keywords.call_args.kwargs
    assert kwargs["filter"] == And([Eq("account_id", "acct"), Eq("uri", "scoped")])
    assert kwargs["mode"] == "bm25"
    assert kwargs["fields"] == ["content"]
    assert kwargs["limit"] == 7 and kwargs["offset"] == 3
    facade._get_backend_for_context.assert_awaited_once_with(ctx)
