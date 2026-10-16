# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import search as routes
from openviking.service.search_service import SearchService
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RetrievalConfig

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)


@pytest.mark.parametrize("model", [routes.FindRequest, routes.SearchRequest])
@pytest.mark.parametrize("value", ["0", "0m", "10m", "12h", "7d", None])
def test_public_decay_duration_valid(model, value):
    assert model(query="release", events_time_decay_protection=value).events_time_decay_protection == value


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
def test_public_search_rejects_unsupported_combinations(fields):
    with pytest.raises(ValidationError):
        routes.SearchRequest(query="release", **fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_http_passes_decay_and_request_clock(monkeypatch, ctx, method):
    captured = []

    async def execute(**kwargs):
        captured.append(kwargs)
        return {"memories": [], "resources": [], "skills": []}

    service = SimpleNamespace(search=SimpleNamespace(**{method: execute}))
    monkeypatch.setattr(routes, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_request_context] = lambda: ctx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/search/{method}", json={
            "query": "release", "events_time_decay_protection": "2d", "score_threshold": 0.3,
        })
        assert response.status_code == 200
        assert captured[-1]["events_time_decay_protection"] == "2d"
        assert captured[-1]["request_now"].tzinfo == timezone.utc
        assert captured[-1]["score_threshold"] == 0.3
        response = await client.post(f"/api/v1/search/{method}", json={"query": "release"})
        assert response.status_code == 200
        assert "events_time_decay_protection" not in captured[-1]
        assert "request_now" not in captured[-1]


@pytest.mark.asyncio
async def test_keywords_http_preserves_independent_engine_records(monkeypatch, ctx):
    keyword_result = {"records": [{"uri": "viking://resources/a", "_score": 4.2}],
                      "ready": True, "engine": "fts", "candidate_complete": False}
    keyword_search = AsyncMock(return_value=keyword_result)
    monkeypatch.setattr(routes, "get_service", lambda: SimpleNamespace(
        search=SimpleNamespace(search_keywords=keyword_search)))
    response = await routes.search(routes.SearchRequest(query="release", search_type="keywords"), ctx)
    assert response["result"] == keyword_result
    keyword_search.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_service_preserves_explicit_request_clock(ctx, method):
    execute = AsyncMock(return_value="result")
    fs = SimpleNamespace(**{method: execute})
    result = await getattr(SearchService(fs), method)(
        "release", ctx, events_time_decay_protection="0", request_now=NOW,
        score_threshold=0.4,
    )
    assert result == "result"
    assert execute.await_args.kwargs["request_now"] is NOW
    assert execute.await_args.kwargs["score_threshold"] == 0.4


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_service_validates_before_using_fs(ctx, method):
    with pytest.raises(InvalidArgumentError, match="events_time_decay_protection"):
        await getattr(SearchService(), method)("release", ctx, events_time_decay_protection="-1h")


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"score_threshold": 0}, {"events_time_decay_protection": "0"}])
async def test_keyword_service_does_not_ignore_ranking_parameters(ctx, options):
    with pytest.raises(InvalidArgumentError):
        await SearchService().search_keywords("release", ctx, **options)


class FakeFS(_SemanticMixin):
    def __init__(self, storage=None):
        self.storage = storage
        self.retrieval_config = RetrievalConfig(enable_intent=False)
        self.rerank_config = None
        self._ensure_retrieval_scope = AsyncMock()

    def _ctx_or_default(self, ctx):
        return ctx

    def _get_vector_store(self):
        return self.storage

    def _get_embedder(self):
        return SimpleNamespace()


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"score_threshold": 0}, {"events_time_decay_protection": "1d"}])
async def test_filter_only_find_rejects_ranking_options(ctx, options):
    with pytest.raises(InvalidArgumentError, match="Filter-only"):
        await FakeFS().find("", ctx=ctx, filter={"op": "must"}, **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["find", "search"])
async def test_service_fs_retriever_proxy_chain(monkeypatch, ctx, method):
    manager = SimpleNamespace(
        _backend_type="opengauss", collection_exists_bound=AsyncMock(return_value=True),
        search_in_tenant=AsyncMock(return_value=[
            {"uri": "viking://resources/above.md", "context_type": "resource", "_score": 0.8},
            {"uri": "viking://resources/below.md", "context_type": "resource", "_score": 0.2},
        ]),
    )
    fs = FakeFS(manager)
    monkeypatch.setattr(fs, "_get_embedder", lambda: SimpleNamespace(
        prepare_embedding_input=lambda value: value,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2]))))
    result = await getattr(SearchService(fs), method)(
        "release", ctx, events_time_decay_protection="1d", request_now=NOW,
        score_threshold=0.5,
    )
    assert [hit.score for hit in result.resources] == [0.8]
    manager.search_in_tenant.assert_awaited_once()
    call = manager.search_in_tenant.await_args
    assert call.args == (ctx,)
    assert call.kwargs["events_time_decay_protection"] == "1d"
    assert call.kwargs["request_now"] is NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("through_service", [True, False])
async def test_search_all_intent_queries_share_one_request_clock(monkeypatch, ctx, through_service):
    from openviking.retrieve.intent_analyzer import IntentAnalyzer
    from openviking_cli.retrieve import QueryPlan

    fs = FakeFS()
    fs.retrieval_config = RetrievalConfig(enable_intent=True)
    queries = [TypedQuery("release", ContextType.RESOURCE, ""), TypedQuery("events", ContextType.MEMORY, "")]
    monkeypatch.setattr(IntentAnalyzer, "analyze", AsyncMock(return_value=QueryPlan(queries=queries, session_context="", reasoning="")))
    calls = []

    async def retrieve(self, query, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(matched_contexts=[])

    monkeypatch.setattr(HierarchicalRetriever, "retrieve", retrieve)
    session = SimpleNamespace(get_context_for_search=AsyncMock(return_value={"current_messages": ["release"]}))
    if through_service:
        await SearchService(fs).search("release", ctx, session=session, events_time_decay_protection="0")
    else:
        await fs.search("release", ctx=ctx, session_info={"current_messages": ["release"]},
                        events_time_decay_protection="0")
    assert len(calls) == 2
    assert calls[0]["request_now"] is calls[1]["request_now"]
    assert calls[0]["request_now"].tzinfo == timezone.utc
    assert all(call["events_time_decay_protection"] == "0" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["local", "http", "vikingdb", "volcengine"])
async def test_non_opengauss_backend_rejected_before_any_query(ctx, backend):
    store = SimpleNamespace(_backend_type=backend)
    retriever = HierarchicalRetriever(store, None)
    with pytest.raises(InvalidArgumentError, match="openGauss"):
        await retriever.retrieve(TypedQuery("release", None, ""), ctx,
                                 events_time_decay_protection="0")


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_mode", [True, False])
@pytest.mark.parametrize("image_query", [True, False])
async def test_thinking_decay_rejected_instead_of_silently_downgraded(ctx, explicit_mode, image_query):
    retriever = HierarchicalRetriever(SimpleNamespace(_backend_type="opengauss"), None)
    retriever._rerank_client = object()
    with pytest.raises(InvalidArgumentError, match="QUICK.*THINKING"):
        await retriever.retrieve(TypedQuery("release", None, "", image_query=image_query), ctx,
                                 mode=RetrieverMode.THINKING if explicit_mode else None,
                                 events_time_decay_protection="1d")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,case", [
    ("find", "invalid"), ("search", "invalid"),
    ("find", "backend"), ("search", "backend"), ("search", "thinking"),
])
async def test_http_errors_are_explicit_without_database(monkeypatch, ctx, method, case):
    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig

    store = SimpleNamespace(_backend_type="local" if case == "backend" else "opengauss")
    fs = FakeFS(store)
    if case == "thinking":
        original_init = HierarchicalRetriever.__init__

        def init_with_rerank(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self._rerank_client = object()

        monkeypatch.setattr(HierarchicalRetriever, "__init__", init_with_rerank)
    service = SimpleNamespace(search=SearchService(fs))
    monkeypatch.setattr(routes, "get_service", lambda: service)
    app = create_app(config=ServerConfig(), service=service)
    app.dependency_overrides[get_request_context] = lambda: ctx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/search/{method}", json={
            "query": "release", "events_time_decay_protection": "-1h" if case == "invalid" else "1d",
        })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    message = response.json()["error"]["message"]
    assert {"invalid": "events_time_decay_protection", "backend": "openGauss", "thinking": "QUICK"}[case] in message


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
    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig
    from tests.server import test_api_search

    # Reuse the route assertions without the module's DB-initializing service fixture.
    service = SimpleNamespace(
        search=SimpleNamespace(find=AsyncMock(), search=AsyncMock()),
        fs=SimpleNamespace(read_visible=AsyncMock()),
    )
    monkeypatch.setattr(routes, "get_service", lambda: service)
    app = create_app(config=ServerConfig(), service=service)
    app.dependency_overrides[get_request_context] = lambda: ctx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await getattr(test_api_search, name)(client=client, service=service, monkeypatch=monkeypatch)


@pytest.mark.asyncio
async def test_default_quick_does_not_add_backend_arguments(ctx):
    store = SimpleNamespace(collection_exists_bound=AsyncMock(return_value=True),
                            search_in_tenant=AsyncMock(return_value=[]))
    retriever = HierarchicalRetriever(store, None)
    await retriever.retrieve(TypedQuery("release", None, ""), ctx)
    assert "events_time_decay_protection" not in store.search_in_tenant.await_args.kwargs
    assert "request_now" not in store.search_in_tenant.await_args.kwargs
