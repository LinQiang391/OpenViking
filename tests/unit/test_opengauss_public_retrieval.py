# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from openviking.models.embedder.base import EmbedResult
from openviking.models.rerank import RerankClient
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.retrieve.intent_analyzer import IntentAnalyzer
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import search as routes
from openviking.service.search_service import SearchService
from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.viking_fs import _semantic
from openviking.storage.viking_fs._grep import _GrepMixin
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType, FindResult, QueryPlan, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig, RetrievalConfig
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
TARGET = "viking://resources/releases"
FILTER = {"op": "must", "field": "search_tags", "conds": ["project=release"]}


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    # Avoid loading personal configuration; monkeypatch restores the previous singleton.
    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", OpenVikingConfig())


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)


@pytest.fixture
def fs_clock(monkeypatch):
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)]
    monkeypatch.setattr(_semantic, "datetime", clock)
    return clock.now


def _records():
    return [
        {"uri": f"{TARGET}/above.md", "context_type": "resource", "level": 2,
         "abstract": "high recall", "_score": 0.8},
        {"uri": f"{TARGET}/below.md", "context_type": "resource", "level": 2,
         "abstract": "low recall", "_score": 0.2},
    ]


def _manager(backend_mode="opengauss"):
    backend = SimpleNamespace(
        _mode=backend_mode,
        collection_name="test_context",
        index_name="test_index",
        collection_exists=AsyncMock(return_value=True),
        query=AsyncMock(side_effect=lambda **kwargs: [] if kwargs.get("advance") else _records()),
        search_by_keywords=AsyncMock(return_value=_records()),
        get_collection_meta=AsyncMock(return_value={
            "Fields": [{"FieldName": "content", "FieldType": "text"}],
            "FullText": [{"Field": "content"}],
        }),
    )
    # Exercise real proxy, tenant scoping and decay fan-out without opening a database.
    manager = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    manager.acl_manager = None
    manager.get_account_backend = AsyncMock(return_value=backend)
    manager._get_backend_for_context = AsyncMock(return_value=backend)
    return manager, backend


class FakeFS(_SemanticMixin, _GrepMixin):
    def __init__(self, storage=None):
        self.storage = storage
        self.retrieval_config = RetrievalConfig(enable_intent=False)
        self.rerank_config = None
        self._fulltext_available = {}
        self._ensure_retrieval_scope = AsyncMock()
        self._require_request_context = Mock(side_effect=lambda ctx: ctx)
        self._vlm_resolver = SimpleNamespace(get_query_planner=AsyncMock(return_value=object()))
        self.embedder = SimpleNamespace(
            prepare_embedding_input=lambda value: value,
            embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
        )
        self._get_embedder = Mock(side_effect=lambda ctx: self.embedder)
        self.abstract = AsyncMock(return_value="release notes")
        self.read = AsyncMock(return_value="release content")
        self.read_visible = AsyncMock(return_value="release content")
        self.overview = AsyncMock(return_value="release overview")

    def _get_vector_store(self):
        return self.storage


def _http_client(monkeypatch, ctx, service):
    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig

    monkeypatch.setattr(routes, "get_service", lambda: service)
    app = create_app(config=ServerConfig(), service=service)
    app.dependency_overrides[get_request_context] = lambda: ctx
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _filter_nodes(expr):
    yield expr
    if isinstance(expr, (And, Or)):
        for child in expr.conds:
            yield from _filter_nodes(child)


def _assert_tenant_scope(expr, ctx, target=TARGET, extra_filter=FILTER):
    nodes = list(_filter_nodes(expr))
    assert Eq("account_id", ctx.account_id) in nodes
    assert PathScope("uri", target, depth=-1) in nodes
    assert RawDSL(extra_filter) in nodes
    assert In("level", [2]) in nodes


@pytest.mark.parametrize("model", [routes.FindRequest, routes.SearchRequest])
@pytest.mark.parametrize("value", ["0", "0m", "10m", "12h", "7d", None])
def test_public_decay_duration_valid(model, value):
    request = model(query="release", events_time_decay_protection=value)
    assert request.events_time_decay_protection == value


@pytest.mark.parametrize("model", [routes.FindRequest, routes.SearchRequest])
@pytest.mark.parametrize("value", ["", "-1d", "1.5h", "1w", "1D", " 1d", "1", 0, True, "999999999999d"])
def test_public_decay_duration_invalid(model, value):
    with pytest.raises(ValidationError, match="events_time_decay_protection"):
        model(query="release", events_time_decay_protection=value)


@pytest.mark.parametrize("fields", [
    {"mode": "context", "events_time_decay_protection": "0"},
    {"search_type": "keywords", "mode": "context"},
    {"search_type": "keywords", "events_time_decay_protection": "1d"},
    {"search_type": "keywords", "score_threshold": 0},
    {"search_type": "keywords", "session_id": "session"},
    {"search_type": "keywords", "read_content": True},
    {"search_type": "keywords", "include_provenance": True},
])
def test_public_search_accepts_unified_search_options(fields):
    request = routes.SearchRequest(query="release", **fields)
    for name, value in fields.items():
        assert getattr(request, name) == value


@pytest.mark.parametrize("fields", [
    {"search_type": "keywords", "query": " "},
    {"search_type": "keywords", "image_url": "https://example.test/image.png"},
    {"mode": "context", "read_content": True},
    {"mode": "context", "target_uri": TARGET},
    {"mode": "list", "max_tokens": 1000},
])
def test_public_search_rejects_actual_unsupported_combinations(fields):
    with pytest.raises(ValidationError):
        routes.SearchRequest(**{"query": "release", **fields})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_http_forwards_decay_without_service_request_clock(monkeypatch, ctx, method):
    execute = AsyncMock(return_value=FindResult(memories=[], resources=[], skills=[]))
    service = SimpleNamespace(search=SimpleNamespace(**{method: execute}))
    async with _http_client(monkeypatch, ctx, service) as client:
        for protection in ("2d", None):
            payload = {"query": "release", "score_threshold": 0.3}
            if protection is not None:
                payload["events_time_decay_protection"] = protection
            response = await client.post(f"/api/v1/search/{method}", json=payload)
            assert response.status_code == 200, response.text
            kwargs = execute.await_args.kwargs
            assert kwargs["events_time_decay_protection"] == protection
            assert kwargs["score_threshold"] == 0.3
            assert kwargs["ctx"] is ctx
            assert "request_now" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
@pytest.mark.parametrize("search_type", ["semantic", "keywords"])
async def test_service_forwards_unified_options_without_request_clock(ctx, method, search_type):
    result = FindResult(memories=[], resources=[], skills=[])
    execute = AsyncMock(return_value=result)
    store = object()
    check = AsyncMock(return_value=True)
    fs = SimpleNamespace(**{method: execute}, _get_vector_store=lambda: store,
                         _collection_has_fulltext=check)
    actual = await getattr(SearchService(fs), method)(
        "release", ctx, search_type=search_type, events_time_decay_protection="0",
        score_threshold=0.4, target_uri=TARGET, filter=FILTER, level=[2],
    )
    assert actual is result
    kwargs = execute.await_args.kwargs
    assert kwargs["search_type"] == search_type
    assert kwargs["events_time_decay_protection"] == "0"
    assert kwargs["score_threshold"] == 0.4
    assert kwargs["ctx"] is ctx
    assert kwargs["target_uri"] == TARGET
    assert kwargs["filter"] == FILTER
    assert kwargs["level"] == [2]
    assert "request_now" not in kwargs
    if method == "search" and search_type == "keywords":
        check.assert_awaited_once_with(
            store, ctx, supported_modes=("volcengine", "vikingdb", "opengauss"), raise_on_error=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_fs_request_clock_reaches_retriever_and_backend(ctx, fs_clock, method):
    manager, backend = _manager()
    fs = FakeFS(manager)
    result = await getattr(SearchService(fs), method)(
        "release", ctx, events_time_decay_protection="1d",
    )
    fs_clock.assert_called_once_with(timezone.utc)
    assert isinstance(result, FindResult)
    advances = [call.kwargs["advance"] for call in backend.query.await_args_list
                if call.kwargs["advance"] is not None]
    assert advances == [{"time_decay": {"protection": "1d", "origin": NOW.isoformat()}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_keywords_capability_check_is_backend_neutral_and_strict(ctx, failure):
    store = object()
    check = AsyncMock(return_value=True)
    if failure:
        check.side_effect = RuntimeError("metadata unavailable")
    fs = SimpleNamespace(_get_vector_store=lambda: store, _collection_has_fulltext=check)
    service = SearchService(fs)
    if failure:
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            await service.ensure_keywords_search_supported(ctx)
    else:
        await service.ensure_keywords_search_supported(ctx)
    check.assert_awaited_once_with(
            store, ctx, supported_modes=("volcengine", "vikingdb", "opengauss"), raise_on_error=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_fs_validates_decay_before_using_storage(ctx, method):
    fs = FakeFS()
    with pytest.raises(ValueError, match="events_time_decay_protection"):
        await getattr(SearchService(fs), method)("release", ctx, events_time_decay_protection="-1h")
    fs._require_request_context.assert_not_called()
    fs._get_embedder.assert_not_called()


@pytest.mark.asyncio
async def test_filter_only_find_rejects_decay_but_keeps_existing_threshold_behavior(ctx):
    fs = FakeFS()
    with pytest.raises(InvalidArgumentError, match="requires a semantic query or image"):
        await fs.find("", ctx=ctx, filter=FILTER, events_time_decay_protection="1d")
    result = FindResult(memories=[], resources=[], skills=[])
    fs._find_by_filter = AsyncMock(return_value=result)
    assert await fs.find("", ctx=ctx, filter=FILTER, score_threshold=0) is result
    assert fs._find_by_filter.await_args.kwargs["filter"] == FILTER


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
@pytest.mark.parametrize("backend_mode", ["opengauss", "local", "vikingdb", "volcengine"])
async def test_semantic_service_fs_retriever_tenant_decay_chain(ctx, fs_clock, method, backend_mode):
    manager, backend = _manager(backend_mode)
    fs = FakeFS(manager)
    result = await getattr(SearchService(fs), method)(
        "release", ctx, target_uri=TARGET, filter=FILTER, level=[2],
        events_time_decay_protection="1d", score_threshold=0.5,
    )
    assert isinstance(result, FindResult)
    assert [(hit.uri, hit.score) for hit in result.resources] == [(f"{TARGET}/above.md", 0.8)]
    assert result.memories == result.skills == []
    fs._require_request_context.assert_called_once_with(ctx)
    fs._get_embedder.assert_called_once_with(ctx)
    fs.embedder.embed_async.assert_awaited_once()
    fs._ensure_retrieval_scope.assert_any_await(TARGET, ctx)
    fs_clock.assert_called_once_with(timezone.utc)
    assert backend.query.await_count == 2
    advances = []
    for call in backend.query.await_args_list:
        kwargs = call.kwargs
        _assert_tenant_scope(kwargs["filter"], ctx)
        assert kwargs["query_vector"] == [0.1, 0.2]
        if kwargs["advance"] is not None:
            advances.append(kwargs["advance"])
    assert advances == [{"time_decay": {"protection": "1d", "origin": NOW.isoformat()}}]
    assert all(call.args == (ctx,) for call in manager._get_backend_for_context.await_args_list)
    backend.search_by_keywords.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
@pytest.mark.parametrize("backend_mode", ["opengauss", "vikingdb", "volcengine"])
@pytest.mark.parametrize("protection", [None, "0", "1d"])
async def test_keywords_service_fs_retriever_tenant_chain(ctx, method, backend_mode, protection):
    manager, backend = _manager(backend_mode)
    fs = FakeFS(manager)
    fs.embedder.embed_async.side_effect = AssertionError("keywords must not embed")
    result = await getattr(SearchService(fs), method)(
        "release", ctx, search_type="keywords", target_uri=TARGET,
        filter=FILTER, level=[2], score_threshold=0.5, events_time_decay_protection=protection,
    )
    assert isinstance(result, FindResult)
    assert [(hit.uri, hit.score) for hit in result.resources] == [(f"{TARGET}/above.md", 0.8)]
    assert set(result.to_dict()) >= {"memories", "resources", "skills", "total"}
    assert "records" not in result.to_dict()
    fs._get_embedder.assert_called_once_with(ctx)
    fs.embedder.embed_async.assert_not_awaited()
    backend.query.assert_not_awaited()
    backend.search_by_keywords.assert_awaited_once()
    kwargs = backend.search_by_keywords.await_args.kwargs
    assert kwargs["query"] == "release"
    assert kwargs["fields"] == ["content"]
    _assert_tenant_scope(kwargs["filter"], ctx)
    assert manager._get_backend_for_context.await_count == (1 if method == "find" else 2)
    assert all(call.args == (ctx,) for call in manager._get_backend_for_context.await_args_list)
    if method == "find":
        backend.get_collection_meta.assert_not_awaited()
    else:
        backend.get_collection_meta.assert_awaited_once_with(raise_on_error=True)


@pytest.mark.asyncio
async def test_keywords_http_uses_unified_search_and_find_result(monkeypatch, ctx):
    manager, backend = _manager()
    fs = FakeFS(manager)
    service = SimpleNamespace(search=SearchService(fs), fs=fs)
    async with _http_client(monkeypatch, ctx, service) as client:
        response = await client.post("/api/v1/search/search", json={
            "query": "release", "search_type": "keywords", "score_threshold": 0.5,
            "read_content": True, "include_provenance": True,
        })
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["total"] == 1
    assert result["resources"][0]["uri"] == f"{TARGET}/above.md"
    assert result["resources"][0]["content"] == "release content"
    assert "records" not in result
    backend.search_by_keywords.assert_awaited_once()
    backend.query.assert_not_awaited()
    fs.embedder.embed_async.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("search_type,protection", [
    ("semantic", None), ("semantic", "1d"), ("keywords", None),
])
@pytest.mark.parametrize("method", ["find", "search"])
async def test_service_quick_and_thinking_keep_rerank_semantics(
    monkeypatch, ctx, method, search_type, protection,
):
    manager, backend = _manager()
    fs = FakeFS(manager)
    fs.rerank_config = RerankConfig(provider="cohere", api_key="test-key", threshold=0.5)
    rerank = Mock(return_value=[0.1, 0.9])
    monkeypatch.setattr(RerankClient, "from_config", Mock(return_value=SimpleNamespace(rerank_batch=rerank)))
    result = await getattr(SearchService(fs), method)(
        "release", ctx, search_type=search_type, limit=1,
        score_threshold=0.5, events_time_decay_protection=protection,
    )
    recall = backend.query if search_type == "semantic" else backend.search_by_keywords
    assert all(call.kwargs["limit"] == (1 if method == "find" else 2)
               for call in recall.await_args_list)
    if method == "find":
        rerank.assert_not_called()
        assert [(hit.uri, hit.score) for hit in result.resources] == [(f"{TARGET}/above.md", 0.8)]
    else:
        rerank.assert_called_once_with("release", ["high recall", "low recall"])
        assert [(hit.uri, hit.score) for hit in result.resources] == [(f"{TARGET}/below.md", 0.9)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, RetrieverMode.THINKING])
@pytest.mark.parametrize("image_query", [True, False])
async def test_thinking_decay_preserves_image_rerank_bypass(ctx, mode, image_query):
    manager, backend = _manager()
    retriever = HierarchicalRetriever(manager, None)
    rerank = Mock(return_value=[0.1, 0.9])
    retriever._rerank_client = SimpleNamespace(rerank_batch=rerank)
    result = await retriever.retrieve(
        TypedQuery("release", None, "", image_query=image_query), ctx,
        mode=mode, limit=1, score_threshold=0.5,
        events_time_decay_protection="1d", request_now=NOW,
    )
    if image_query:
        rerank.assert_not_called()
        assert result.matched_contexts[0].score == 0.8
    else:
        rerank.assert_called_once_with("release", ["high recall", "low recall"])
        assert result.matched_contexts[0].score == 0.9
    assert all(call.kwargs["limit"] == (1 if image_query else 2)
               for call in backend.query.await_args_list)
    assert any(call.kwargs["advance"] == {
        "time_decay": {"protection": "1d", "origin": NOW.isoformat()},
    } for call in backend.query.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("through_service", [True, False])
async def test_search_all_intent_queries_share_one_fs_request_clock(
    monkeypatch, ctx, fs_clock, through_service,
):
    manager, backend = _manager()
    fs = FakeFS(manager)
    fs.retrieval_config = RetrievalConfig(enable_intent=True)
    queries = [TypedQuery("release", ContextType.RESOURCE, ""),
               TypedQuery("events", ContextType.MEMORY, "")]
    plan = QueryPlan(queries=queries, session_context="", reasoning="")
    analyze = AsyncMock(return_value=plan)
    monkeypatch.setattr(IntentAnalyzer, "analyze", analyze)
    manager.search_in_tenant = AsyncMock(wraps=manager.search_in_tenant)
    session_info = {"current_messages": ["release"]}
    session = SimpleNamespace(get_context_for_search=AsyncMock(return_value=session_info))
    if through_service:
        result = await SearchService(fs).search(
            "release", ctx, session=session, events_time_decay_protection="0",
        )
        session.get_context_for_search.assert_awaited_once_with("release")
    else:
        result = await fs.search("release", ctx=ctx, session_info=session_info,
                                 events_time_decay_protection="0")
    fs._vlm_resolver.get_query_planner.assert_awaited_once_with(ctx.account_id)
    analyze.assert_awaited_once()
    assert result.query_plan is plan
    assert [item.query for item in result.query_results] == queries
    calls = manager.search_in_tenant.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["request_now"] is calls[1].kwargs["request_now"] is NOW
    assert all(call.args == (ctx,) for call in calls)
    assert all(call.kwargs["events_time_decay_protection"] == "0" for call in calls)
    assert {call.kwargs["context_type"] for call in calls} == {"resource", "memory"}
    fs_clock.assert_called_once_with(timezone.utc)
    assert backend.query.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("search_type", ["semantic", "keywords"])
async def test_context_http_runs_real_assembly_and_retrieval_chain(monkeypatch, ctx, search_type):
    manager, backend = _manager()
    fs = FakeFS(manager)
    service = SimpleNamespace(search=SearchService(fs), fs=fs)
    async with _http_client(monkeypatch, ctx, service) as client:
        response = await client.post("/api/v1/search/search", json={
            "query": "release", "mode": "context", "search_type": search_type,
            "events_time_decay_protection": "1d" if search_type == "semantic" else None,
            "score_threshold": 0.5,
            "query_expansion": "off", "peer_scope": "actor", "max_tokens": 1000,
        })
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [entry["uri"] for entry in result["entries"]] == [f"{TARGET}/above.md"]
    assert result["entries"][0]["score"] == 0.8
    assert result["stats"]["used_tokens"] <= 1000
    assert not result["stats"].get("retrieval_errors")
    assert "rendered" in result
    assert "records" not in result
    if search_type == "keywords":
        backend.search_by_keywords.assert_awaited_once()
        backend.query.assert_not_awaited()
        fs.embedder.embed_async.assert_not_awaited()
    else:
        assert backend.query.await_count == 2
        assert any(call.kwargs["advance"]["time_decay"]["protection"] == "1d"
                   for call in backend.query.await_args_list if call.kwargs["advance"])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_http_invalid_decay_is_explicit_without_database(monkeypatch, ctx, method):
    execute = AsyncMock()
    service = SimpleNamespace(search=SimpleNamespace(**{method: execute}))
    async with _http_client(monkeypatch, ctx, service) as client:
        response = await client.post(f"/api/v1/search/{method}", json={
            "query": "release", "events_time_decay_protection": "-1h",
        })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert "events_time_decay_protection" in response.json()["error"]["message"]
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "test_find_omits_content_when_read_is_not_requested",
    "test_find_keeps_hit_without_content_when_read_fails",
    "test_find_with_target_uri_and_tags_passes_target_and_filter",
    "test_find_with_level_passes_to_service",
    "test_find_without_level_passes_none",
    "test_search_with_level_passes_to_service",
    "test_search_without_level_passes_none",
    "test_find_with_level_string_input",
    "test_find_with_level_int_input",
    "test_find_with_since_compiles_time_range",
    "test_find_combines_existing_filter_with_time_range",
    "test_find_with_context_type_compiles_filter",
    "test_find_combines_tags_with_existing_filter",
    "test_search_combines_context_type_list_with_existing_filter",
    "test_search_compiles_tags_only_filter",
    "test_search_with_until_compiles_time_range",
])
async def test_existing_search_routes_with_no_database(monkeypatch, ctx, name):
    from tests.server import test_api_search

    # Reuse route assertions without the module's database-initializing fixture.
    service = SimpleNamespace(
        search=SimpleNamespace(find=AsyncMock(), search=AsyncMock()),
        fs=SimpleNamespace(read_visible=AsyncMock()),
    )
    async with _http_client(monkeypatch, ctx, service) as client:
        await getattr(test_api_search, name)(client=client, service=service, monkeypatch=monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "test_context_mode_returns_flat_entries_within_budget",
    "test_context_mode_forwards_keywords_to_every_internal_search",
    "test_context_mode_quotas_use_category_ownership_roots",
])
async def test_existing_context_routes_with_no_database(monkeypatch, ctx, name):
    from tests.server import test_api_search_context

    fs = FakeFS()
    service = SimpleNamespace(
        search=SimpleNamespace(
            find=AsyncMock(return_value=FindResult(memories=[], resources=[], skills=[])),
            find_skills=AsyncMock(return_value=FindResult(memories=[], resources=[], skills=[])),
            ensure_keywords_search_supported=AsyncMock(),
            is_intent_enabled=lambda: False,
        ),
        fs=fs,
    )
    default_ctx = RequestContext(user=UserIdentifier("account", "default"), role=Role.USER)
    async with _http_client(monkeypatch, default_ctx, service) as client:
        await getattr(test_api_search_context, name)(client=client, service=service, monkeypatch=monkeypatch)


@pytest.mark.asyncio
async def test_default_quick_does_not_add_decay_backend_arguments(ctx):
    manager, backend = _manager()
    manager.search_in_tenant = AsyncMock(wraps=manager.search_in_tenant)
    retriever = HierarchicalRetriever(manager, None)
    await retriever.retrieve(TypedQuery("release", None, ""), ctx)
    kwargs = manager.search_in_tenant.await_args.kwargs
    assert "events_time_decay_protection" not in kwargs
    assert "request_now" not in kwargs
    backend.query.assert_awaited_once()
    assert backend.query.await_args.kwargs["advance"] is None
