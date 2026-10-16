# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking.storage.collection_schemas import (
    TextEmbeddingHandler,
    backfill_content,
    materialize_content_only,
)
from openviking.storage.expr import Eq
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import (
    VIKINGDB_CONTENT_MAX_SIZE,
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("acct", "user"), role=Role.USER)


def make_backend(capabilities, mode="opengauss"):
    meta = {
        "Fields": [
            {"FieldName": "id"},
            {"FieldName": "content", "FieldType": "text"},
            {"FieldName": "account_id"},
        ],
        "RetrievalCapabilities": capabilities,
    }
    adapter = SimpleNamespace(
        mode=mode,
        USE_CONTENT_FIELD=mode in {"vikingdb", "volcengine"},
        get_collection=lambda: SimpleNamespace(get_meta_data=lambda: meta),
        collection_exists=lambda: True,
        search_by_keywords=Mock(return_value=[{"id": "one", "_score": 1.5}]),
    )
    backend = _SingleAccountBackend(
        VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acct",
        shared_adapter=adapter,
    )
    return backend, meta, adapter


@pytest.mark.parametrize("enabled", [False, True])
def test_content_write_policy_uses_actual_metadata_without_opengauss_truncation(enabled):
    backend, meta, _ = make_backend({"content": enabled})
    text = "正文" * VIKINGDB_CONTENT_MAX_SIZE
    for prepare in (backend._prepare_upsert_payload, backend._prepare_update_payload):
        source = {"id": "one", "content": text}
        result = prepare(source)
        assert result.get("content") == (text if enabled else None)
        assert source["content"] == text
    meta["RetrievalCapabilities"]["content"] = not enabled
    assert ("content" in backend._prepare_update_payload({"content": text})) is not enabled


@pytest.mark.parametrize("mode", ["local", "volcengine", "vikingdb"])
def test_other_backends_preserve_existing_content_policy(mode):
    backend, _, _ = make_backend({"content": True}, mode=mode)
    text = "x" * (VIKINGDB_CONTENT_MAX_SIZE + 9)
    result = backend._prepare_update_payload({"content": text})
    assert result.get("content") == (None if mode == "local" else text[:VIKINGDB_CONTENT_MAX_SIZE])


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["bm25", "fts"])
async def test_keyword_query_uses_collection_engine_not_configuration(engine):
    backend, meta, adapter = make_backend({"content": True, engine: True})
    result = await backend.search_by_keywords(query="正文", filter=Eq("level", 2))
    assert result == [{"id": "one", "_score": 1.5}]
    options = adapter.search_by_keywords.call_args.kwargs
    assert options["mode"] == engine
    assert options["fields"] == ["content"]
    assert "acct" in str(options["filter"])
    meta["RetrievalCapabilities"][engine] = False
    with pytest.raises(NotImplementedError, match="not ready"):
        await backend.search_by_keywords(query="正文")
    assert adapter.search_by_keywords.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [{}, {"content": True}, {"bm25": True}])
async def test_keywords_fail_closed_for_unready_collection(capabilities):
    backend, _, adapter = make_backend(capabilities)
    with pytest.raises(NotImplementedError):
        await backend.search_by_keywords(query="query")
    adapter.search_by_keywords.assert_not_called()


@pytest.mark.asyncio
async def test_keywords_reject_non_content_fields_and_non_lexical_modes():
    backend, _, adapter = make_backend({"content": True, "fts": True})
    for options in ({"fields": ["abstract"]}, {"mode": "hybrid"}):
        with pytest.raises(InvalidArgumentError):
            await backend.search_by_keywords(query="query", **options)
    adapter.search_by_keywords.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_bm25_cannot_silently_fallback_to_fts():
    backend, _, adapter = make_backend({"content": True, "fts": True})
    with pytest.raises(NotImplementedError, match="bm25"):
        await backend.search_by_keywords(query="query", mode="bm25")
    adapter.search_by_keywords.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_vikingdb_keyword_call_is_unchanged():
    backend, _, adapter = make_backend({}, mode="vikingdb")
    await backend.search_by_keywords(query="legacy")
    assert "mode" not in adapter.search_by_keywords.call_args.kwargs
    assert "fields" not in adapter.search_by_keywords.call_args.kwargs


@pytest.mark.asyncio
async def test_facade_content_and_keyword_info_are_account_scoped(ctx):
    backend, meta, _ = make_backend({"content": True, "fts": True})
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._get_backend_for_context = Mock(return_value=backend)
    assert await facade.get_content_policy(ctx=ctx) == {"enabled": True, "max_size": None}
    assert await facade.get_keyword_search_info(ctx=ctx) == {
        "ready": True, "engine": "fts", "candidate_complete": False
    }
    meta["RetrievalCapabilities"] = {"content": False}
    assert not (await facade.get_keyword_search_info(ctx=ctx))["ready"]
    assert all(call.args == (ctx,) for call in facade._get_backend_for_context.call_args_list)


@pytest.mark.asyncio
async def test_facade_keyword_mode_and_fields_reach_bound_backend(ctx):
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    backend = SimpleNamespace(search_by_keywords=AsyncMock(return_value=[]))
    facade._get_backend_for_context = Mock(return_value=backend)
    facade._acl_enabled = AsyncMock(return_value=False)
    await facade.search_by_keywords(
        query="query", mode="bm25", fields=["content"], ctx=ctx
    )
    options = backend.search_by_keywords.call_args.kwargs
    assert options["mode"] == "bm25"
    assert options["fields"] == ["content"]
    assert "acct" in str(options["filter"])


@pytest.mark.asyncio
async def test_tenant_keyword_search_reuses_scope_filter(ctx):
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._acl_enabled = AsyncMock(return_value=True)
    facade._build_scope_filter = Mock(return_value=Eq("uri", "scoped"))
    facade.search_by_keywords = AsyncMock(return_value=[])
    await facade.search_keywords_in_tenant(
        ctx, "query", target_directories=["viking://resources/repo"],
        extra_filter={"tag": "value"}, level=[2], limit=7, offset=3
    )
    options = facade._build_scope_filter.call_args.kwargs
    assert options["acl_enabled"] is True
    assert options["level"] == [2]
    assert facade.search_by_keywords.call_args.kwargs["filter"] == Eq("uri", "scoped")
    assert facade.search_by_keywords.call_args.kwargs["ctx"] is ctx


@pytest.mark.asyncio
async def test_service_keywords_is_explicit_scoped_api_without_embedding(ctx):
    info = {"ready": True, "engine": "fts", "candidate_complete": False}
    store = SimpleNamespace(
        get_keyword_search_info=AsyncMock(return_value=info),
        search_keywords_in_tenant=AsyncMock(return_value=[{"uri": "one", "_score": 2.0}]),
    )
    fs = SimpleNamespace(
        _get_vector_store=lambda: store, _ensure_retrieval_scope=AsyncMock(),
        _get_embedder=Mock(side_effect=AssertionError("must not embed")),
    )
    result = await SearchService(fs).search_keywords(
        "query", ctx, target_uri="viking://resources/repo", level=[2], limit=3
    )
    assert result == {"records": [{"uri": "one", "_score": 2.0}], **info}
    fs._ensure_retrieval_scope.assert_awaited_once_with("viking://resources/repo", ctx)
    options = store.search_keywords_in_tenant.call_args.kwargs
    assert options["ctx"] is ctx
    assert options["target_directories"] == ["viking://resources/repo"]
    assert options["level"] == [2]
    fs._get_embedder.assert_not_called()
    store.get_keyword_search_info.return_value = {**info, "ready": False}
    with pytest.raises(NotImplementedError):
        await SearchService(fs).search_keywords("query", ctx)
    assert store.search_keywords_in_tenant.await_count == 1


@pytest.mark.asyncio
async def test_opengauss_grep_uses_fs_even_if_lexical_ready(ctx):
    fs = VikingFS(agfs=SimpleNamespace())
    store = SimpleNamespace(_backend_type="opengauss", get_collection_meta=AsyncMock())
    fs._get_vector_store = lambda: store
    fs._collection_has_fulltext = AsyncMock(return_value=True)
    fs._get_cached_count = AsyncMock(return_value=1000000)
    assert await fs._resolve_grep_engine("auto", "viking://resources", ctx, 0) == "fs"
    fs._collection_has_fulltext.assert_not_awaited()
    fs._get_cached_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_only_materializer_reads_full_source_without_embedding(ctx, monkeypatch):
    text = "源正文" * VIKINGDB_CONTENT_MAX_SIZE
    fs = SimpleNamespace(read_file=AsyncMock(return_value=text))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.utils.embedding_utils._resolve_resource_content_type",
        AsyncMock(return_value=None),
    )
    message = EmbeddingMsg(message="short embedding", context_data={
        "uri": "viking://resources/full.txt", "context_type": "resource", "is_leaf": True
    })
    assert await materialize_content_only(message, ctx) == text
    assert await TextEmbeddingHandler._materialize_content(message, ctx) == text[:VIKINGDB_CONTENT_MAX_SIZE]


@pytest.mark.asyncio
async def test_embedding_queue_uses_account_content_policy(ctx, monkeypatch):
    from openviking.models.embedder.base import EmbedResult

    config = SimpleNamespace(
        storage=SimpleNamespace(vectordb=SimpleNamespace(name="context")),
        embedding=SimpleNamespace(
            dimension=2,
            circuit_breaker=SimpleNamespace(
                failure_threshold=5, reset_timeout=60.0, max_reset_timeout=600.0
            ),
        ),
    )
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    store = SimpleNamespace(
        is_closing=False,
        uses_content_field=False,
        get_content_policy=AsyncMock(return_value={"enabled": True, "max_size": None}),
        upsert=AsyncMock(return_value="one"),
    )
    text = "x" * (VIKINGDB_CONTENT_MAX_SIZE + 100)
    fs = SimpleNamespace(read_file=AsyncMock(return_value=text))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.utils.embedding_utils._resolve_resource_content_type",
        AsyncMock(return_value=None),
    )
    message = EmbeddingMsg(message="embedding text", context_data={
        "uri": "viking://resources/full.txt", "context_type": "resource",
        "is_leaf": True, "account_id": ctx.account_id, "level": 2, "abstract": "short",
    })
    handler = TextEmbeddingHandler(store)
    handler._embedder = SimpleNamespace(
        prepare_embedding_input=lambda content: content,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
    )
    outcome = await handler.on_dequeue({"data": message.to_json()})
    assert outcome.outcome is ProcessOutcome.SUCCESS
    assert store.upsert.call_args.args[0]["content"] == text
    assert store.get_content_policy.call_args.kwargs["ctx"].account_id == ctx.account_id


@pytest.mark.asyncio
async def test_content_backfill_is_bounded_partial_and_preserves_vectors(ctx, monkeypatch):
    text = "x" * (VIKINGDB_CONTENT_MAX_SIZE + 12)
    record = {"id": "one", "uri": "viking://resources/full.txt", "level": 2,
              "account_id": "acct", "context_type": "resource", "vector": [0.1, 0.2]}
    fs = SimpleNamespace(read_file=AsyncMock(return_value=text))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.utils.embedding_utils._resolve_resource_content_type",
        AsyncMock(return_value=None),
    )
    store = SimpleNamespace(
        get_content_policy=AsyncMock(return_value={"enabled": True, "max_size": None}),
        get_strict=AsyncMock(return_value=[record]),
        backfill_content_record=AsyncMock(return_value=True),
    )
    assert await backfill_content(store, ["one"], ctx=ctx) == {"requested": 1, "updated": 1}
    store.backfill_content_record.assert_awaited_once_with(record, text, ctx=ctx)
    assert record["vector"] == [0.1, 0.2]
    with pytest.raises(ValueError, match="at most"):
        await backfill_content(store, ["one", "two"], ctx=ctx, max_records=1)
    record["account_id"] = "other"
    with pytest.raises(PermissionError):
        await backfill_content(store, ["one"], ctx=ctx)
    assert store.backfill_content_record.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("uri,level,expected", [
    ("viking://resources/dir", 0, "viking://resources/dir/.abstract.md"),
    ("viking://resources/dir/.overview.md", 1, "viking://resources/dir/.overview.md"),
])
async def test_content_backfill_resolves_summary_uri(ctx, monkeypatch, uri, level, expected):
    fs = SimpleNamespace(read_file=AsyncMock(return_value="summary"))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    store = SimpleNamespace(
        get_content_policy=AsyncMock(return_value={"enabled": True, "max_size": None}),
        get_strict=AsyncMock(return_value=[{
            "id": "one", "account_id": "acct", "uri": uri, "level": level
        }]),
        backfill_content_record=AsyncMock(return_value=True),
    )
    await backfill_content(store, ["one"], ctx=ctx)
    fs.read_file.assert_awaited_once_with(expected, ctx=ctx)
