# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""6006fc340 compatibility through the real multi-account vector facade."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool
from openviking.storage.expr import And, PathScope, RawDSL
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_fs import _grep as grep_module
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.grep_config import GrepConfig

ROOT = "viking://resources"
FILE = ROOT + "/a.md"
TAG_FILTER = {"op": "must", "field": "search_tags", "conds": ["env=prod"]}
META = {
    "Fields": [{"FieldName": "content", "FieldType": "text"}],
    "FullText": [{"Field": "content"}],
}
READY = {"ready": True, "engine": "bm25", "candidate_complete": False}
pytestmark = pytest.mark.asyncio


def _ctx(account="account"):
    return RequestContext(user=UserIdentifier(account, "user"), role=Role.USER)


def _backend(mode):
    return SimpleNamespace(
        _mode=mode,
        collection_name="context",
        index_name="default",
        get_collection_meta=AsyncMock(return_value=META),
        get_keyword_search_info=AsyncMock(return_value=dict(READY)),
        search_by_keywords=AsyncMock(return_value=[{"uri": FILE, "search_tags": ["env=prod"]}]),
        query=AsyncMock(return_value=[{"uri": FILE, "search_tags": ["env=prod"]}]),
        count=AsyncMock(return_value=10000),
    )


def _facade(backends, global_mode="opengauss"):
    # Avoid constructing external adapters; keep real account resolution and all proxy methods.
    store = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    store._backend_type = global_mode
    store._collection_name = "context"
    store._index_name = "default"
    store._resolved_backends = backends
    store._account_backend_locks = KeyedAsyncLockPool()
    store._initializing_accounts = set()
    store._closing = False
    store._vector_config_resolver = object()
    store.acl_manager = None
    return store


def _fs(store, threshold=0, engine="auto"):
    fs = VikingFS(
        agfs=SimpleNamespace(),
        vector_store=store,
        grep_config=GrepConfig(engine=engine, switch_to_remote_threshold=threshold),
    )
    fs._async_agfs.stat = AsyncMock(return_value={"name": "resources", "isDir": True})
    fs._async_agfs.read = AsyncMock(return_value=b"error\nERR code 42\nC++\nerror again")
    fs._async_agfs.grep = AsyncMock(return_value={"matches": [], "files_scanned": 9})
    fs._async_agfs.ls = AsyncMock(return_value=[{"name": "a.md", "isDir": False}])
    return fs


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb"])
async def test_positive_cache_survives_metadata_failure_even_with_real_info_method(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    service = SearchService(fs)
    await service.ensure_keywords_search_supported(_ctx())
    backend.get_collection_meta.side_effect = RuntimeError("metadata unavailable")
    backend.get_keyword_search_info.side_effect = RuntimeError("must not bypass old cache")

    await service.ensure_keywords_search_supported(_ctx())
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 0) == "vikingdb_then_fs"
    assert (await fs.grep(ROOT, "error", ctx=_ctx()))["count"] == 2
    assert backend.get_collection_meta.await_count == 1
    backend.get_keyword_search_info.assert_not_awaited()
    assert list(fs._fulltext_available.values()) == [(True, None)]


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb"])
async def test_negative_cache_has_original_sixty_second_ttl(monkeypatch, mode):
    backend = _backend(mode)
    backend.get_collection_meta.side_effect = [{**META, "FullText": []}, META]
    fs = _fs(_facade({"account": backend}))
    now = 1000.0
    monkeypatch.setattr(grep_module.time, "monotonic", lambda: now)
    assert not await fs._collection_has_fulltext(fs.vector_store, _ctx())
    now = 1059.0
    assert not await fs._collection_has_fulltext(fs.vector_store, _ctx(), raise_on_error=True)
    assert backend.get_collection_meta.await_count == 1
    now = 1060.0
    assert await fs._collection_has_fulltext(fs.vector_store, _ctx())
    assert backend.get_collection_meta.await_count == 2
    backend.get_keyword_search_info.assert_not_awaited()


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb"])
async def test_metadata_errors_and_invalid_metadata_are_not_negative_cached(mode):
    backend = _backend(mode)
    backend.get_collection_meta.side_effect = [RuntimeError("metadata unavailable"), {}, META]
    fs = _fs(_facade({"account": backend}))
    assert not await fs._collection_has_fulltext(fs.vector_store, _ctx())
    assert not fs._fulltext_available
    with pytest.raises(RuntimeError, match="invalid collection metadata"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx())
    assert not fs._fulltext_available
    await SearchService(fs).ensure_keywords_search_supported(_ctx())
    backend.get_collection_meta.assert_awaited_with(raise_on_error=True)
    backend.get_keyword_search_info.assert_not_awaited()


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb"])
async def test_strict_metadata_failure_propagates_without_caching(mode):
    backend = _backend(mode)
    backend.get_collection_meta.side_effect = RuntimeError("metadata unavailable")
    fs = _fs(_facade({"account": backend}))
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx())
    assert not fs._fulltext_available
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 0) == "fs"
    assert backend.get_collection_meta.await_count == 2
    backend.get_keyword_search_info.assert_not_awaited()


async def test_cache_identity_stays_scoped_by_account_collection_and_index():
    first, second = _backend("vikingdb"), _backend("vikingdb")
    second.get_collection_meta.return_value = {**META, "FullText": []}
    fs = _fs(_facade({"first": first, "second": second}))
    assert await fs._collection_has_fulltext(fs.vector_store, _ctx("first"))
    assert not await fs._collection_has_fulltext(fs.vector_store, _ctx("second"))
    first.collection_name = "replacement"
    first.get_collection_meta.return_value = {**META, "FullText": []}
    assert not await fs._collection_has_fulltext(fs.vector_store, _ctx("first"))
    first.index_name = "replacement_index"
    first.get_collection_meta.return_value = META
    assert await fs._collection_has_fulltext(fs.vector_store, _ctx("first"))
    assert len(fs._fulltext_available) == 4


@pytest.mark.parametrize("mode", ["local", "unknown", "opengauss", "vikingdb"])
async def test_supported_modes_are_checked_before_info_or_cache(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    fs._fulltext_available[("account", mode, "context", "default")] = (True, None)
    assert not await fs._collection_has_fulltext(
        fs.vector_store, _ctx(), supported_modes=("volcengine",), raise_on_error=True
    )
    backend.get_keyword_search_info.assert_not_awaited()
    backend.get_collection_meta.assert_not_awaited()


async def test_ensure_rejects_local_even_if_info_and_metadata_claim_ready():
    backend = _backend("local")
    fs = _fs(_facade({"account": backend}))
    with pytest.raises(InvalidArgumentError, match="not supported"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx())
    backend.get_keyword_search_info.assert_not_awaited()
    backend.get_collection_meta.assert_not_awaited()


async def test_opengauss_info_is_live_and_scoped_to_actual_account():
    cloud, gauss = _backend("vikingdb"), _backend("opengauss")
    fs = _fs(_facade({"cloud": cloud, "gauss": gauss}, global_mode="local"))
    gauss.get_keyword_search_info.side_effect = [READY, {**READY, "ready": False}]
    await SearchService(fs).ensure_keywords_search_supported(_ctx("cloud"))
    await SearchService(fs).ensure_keywords_search_supported(_ctx("gauss"))
    with pytest.raises(InvalidArgumentError, match="not supported"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx("gauss"))
    assert gauss.get_keyword_search_info.await_count == 2
    gauss.get_collection_meta.assert_not_awaited()
    cloud.get_keyword_search_info.assert_not_awaited()
    assert list(fs._fulltext_available) == [("cloud", "vikingdb", "context", "default")]


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb"])
@pytest.mark.parametrize("pattern", ["error", "ERR.*42", "C++", "", "   "])
@pytest.mark.parametrize("node_limit", [None, 1])
@pytest.mark.parametrize("tags", [False, True])
async def test_cloud_public_grep_preserves_baseline_recall_and_tag_routes(
    mode, pattern, node_limit, tags
):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    options = {"tag_filter": TAG_FILTER, "include_tags": True} if tags else {}
    result = await fs.grep(ROOT, pattern, ctx=_ctx(), node_limit=node_limit, **options)
    call = backend.search_by_keywords.await_args.kwargs
    assert call["query"] == pattern.strip()
    assert call["limit"] == (5 if node_limit else 100000)
    assert call["output_fields"] == (["uri", "search_tags"] if tags else ["uri"])
    assert call["mode"] is None and call["fields"] is None
    assert PathScope("uri", ROOT, depth=10) in _filter_nodes(call["filter"])
    if tags:
        assert RawDSL(TAG_FILTER) in _filter_nodes(call["filter"])
        assert all(match["tags"] == ["env=prod"] for match in result["matches"])
    expected = {"error": 2, "ERR.*42": 1, "C++": 1, "": 4, "   ": 0}[pattern]
    assert result["count"] == (min(expected, node_limit) if node_limit else expected)
    fs._async_agfs.grep.assert_not_awaited()
    backend.get_keyword_search_info.assert_not_awaited()
    backend.query.assert_not_awaited()


def _filter_nodes(expr):
    yield expr
    if isinstance(expr, And):
        for child in expr.conds:
            yield from _filter_nodes(child)


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb", "local"])
async def test_non_gauss_threshold_fs_never_attempts_incomplete_priority(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}), threshold=10001)
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=1)
    assert result["files_scanned"] == 9
    backend.search_by_keywords.assert_not_awaited()
    backend.get_keyword_search_info.assert_not_awaited()
    assert backend.get_collection_meta.await_count == (0 if mode == "local" else 1)


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb", "local"])
async def test_non_gauss_priority_helper_never_probes_keyword_info(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    assert await fs._grep_keyword_priority(ROOT, "error", None, False, 1, 10, _ctx(), 0) is None
    backend.get_keyword_search_info.assert_not_awaited()
    backend.search_by_keywords.assert_not_awaited()


@pytest.mark.parametrize("mode", ["volcengine", "vikingdb"])
async def test_resolver_keeps_count_cache_threshold_zero_and_failure_behavior(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 0) == "vikingdb_then_fs"
    backend.count.assert_not_awaited()
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 10001) == "fs"
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 10000) == "vikingdb_then_fs"
    assert backend.count.await_count == 1
    fs._count_cache.clear()
    backend.count.side_effect = RuntimeError("count failed")
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 1) == "fs"
    backend.get_keyword_search_info.assert_not_awaited()


@pytest.mark.parametrize("mode", ["local", "vikingdb", "volcengine", "opengauss"])
@pytest.mark.parametrize("route", ["fs", "projection", "session", "force_fs"])
async def test_fs_routes_never_probe_backend(mode, route):
    backend = _backend(mode)
    store = _facade({"account": backend})
    store.get_account_backend = AsyncMock(side_effect=AssertionError("must not resolve backend"))
    fs = _fs(store, engine="fs" if route == "fs" else "auto")
    fs._get_backend_mode = AsyncMock(side_effect=AssertionError("must not probe mode"))
    fs._get_vector_store = Mock(side_effect=AssertionError("must not access vector store"))
    options = {"content_transform": lambda text, uri: text} if route == "projection" else {}
    if route == "force_fs":
        options["force_fs"] = True
    uri = "viking://user/user/sessions/s1" if route == "session" else ROOT
    await fs.grep(uri, "error", ctx=_ctx(), node_limit=1, **options)
    fs._get_vector_store.assert_not_called()
    fs._get_backend_mode.assert_not_awaited()
    store.get_account_backend.assert_not_awaited()
    backend.get_collection_meta.assert_not_awaited()
    backend.get_keyword_search_info.assert_not_awaited()
    backend.search_by_keywords.assert_not_awaited()


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("pattern", ["ERR.*42", "C++"])
async def test_only_actual_opengauss_forces_regex_fs(complete, pattern):
    backend = _backend("opengauss")
    backend.get_keyword_search_info.return_value = {**READY, "candidate_complete": complete}
    store = _facade({"account": backend}, global_mode="vikingdb")
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store)
    fs._get_backend_mode = AsyncMock(wraps=fs._get_backend_mode)
    await fs.grep(ROOT, pattern, ctx=_ctx(), node_limit=1)
    fs._get_backend_mode.assert_awaited_once_with(store, _ctx())
    store.get_account_backend.assert_awaited_once_with("account")
    fs._async_agfs.grep.assert_awaited_once()
    backend.search_by_keywords.assert_not_awaited()
    backend.get_keyword_search_info.assert_not_awaited()


async def test_only_actual_opengauss_uses_incomplete_priority():
    backend = _backend("opengauss")
    fs = _fs(_facade({"account": backend}, global_mode="vikingdb"))
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=1)
    assert result["count"] == 1
    assert backend.search_by_keywords.await_args.kwargs["mode"] == "bm25"
    assert backend.search_by_keywords.await_args.kwargs["fields"] == ["content"]
    fs._async_agfs.grep.assert_not_awaited()
    assert backend.get_keyword_search_info.await_count == 2


@pytest.mark.parametrize("global_mode", ["local", "vikingdb", "opengauss"])
@pytest.mark.parametrize("mode", ["local", "volcengine", "vikingdb", "opengauss"])
async def test_filter_only_find_never_adds_keyword_preflight(monkeypatch, mode, global_mode):
    backend = _backend(mode)
    backend.get_collection_meta.side_effect = AssertionError("must not add find preflight")
    backend.get_keyword_search_info.side_effect = AssertionError("must not probe readiness")
    store = _facade({"account": backend}, global_mode=global_mode)
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store)
    mode_probe = AsyncMock(side_effect=AssertionError("must not probe mode"))
    monkeypatch.setattr(VikingFS, "_get_backend_mode", mode_probe)
    service = SearchService(fs)
    service.ensure_keywords_search_supported = AsyncMock(
        side_effect=AssertionError("must not add find preflight")
    )
    result = await service.find(
        "", _ctx(), target_uri=ROOT, filter=TAG_FILTER, search_type="keywords"
    )
    assert result.total == 1
    backend.query.assert_awaited_once()
    store.get_account_backend.assert_awaited_once_with("account")
    mode_probe.assert_not_awaited()
    service.ensure_keywords_search_supported.assert_not_awaited()
    backend.get_keyword_search_info.assert_not_awaited()
    backend.get_collection_meta.assert_not_awaited()


@pytest.mark.parametrize("global_mode", ["local", "vikingdb", "opengauss"])
async def test_shared_grep_resolves_mode_once_per_actual_account(global_mode):
    backends = {mode: _backend(mode) for mode in ("volcengine", "vikingdb", "opengauss", "local")}
    store = _facade(backends, global_mode=global_mode)
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store)
    fs._get_backend_mode = AsyncMock(wraps=fs._get_backend_mode)
    for mode in ("opengauss", "vikingdb", "local", "volcengine", "opengauss"):
        backend = backends[mode]
        store.get_account_backend.reset_mock()
        fs._get_backend_mode.reset_mock()
        backend.get_collection_meta.reset_mock()
        backend.get_keyword_search_info.reset_mock()
        backend.search_by_keywords.reset_mock()
        backend.count.reset_mock()
        result = await fs.grep(ROOT, "error", ctx=_ctx(mode), node_limit=1)
        fs._get_backend_mode.assert_awaited_once_with(store, _ctx(mode))
        # Include existing facade dispatches, not only the engine-selection probe.
        expected = 1 if mode == "local" else 4
        assert store.get_account_backend.await_args_list == [call(mode)] * expected
        assert backend.get_collection_meta.await_count == (mode in ("volcengine", "vikingdb"))
        assert backend.get_keyword_search_info.await_count == (2 if mode == "opengauss" else 0)
        assert backend.search_by_keywords.await_count == (mode != "local")
        backend.count.assert_not_awaited()
        assert result["count"] == (0 if mode == "local" else 1)


@pytest.mark.parametrize("ctx", [None, SimpleNamespace(account_id="")])
async def test_no_account_fallback_prefers_backend_type(ctx):
    store = SimpleNamespace(
        _backend_type="local",
        _mode="opengauss",
        get_account_backend=AsyncMock(side_effect=AssertionError("no account to resolve")),
        get_keyword_search_info=AsyncMock(side_effect=AssertionError("must not probe readiness")),
    )
    fs = _fs(store)
    assert await fs._get_backend_mode(store, ctx) == "local"
    assert await fs._resolve_grep_engine("auto", ROOT, ctx, 0) == "fs"
    del store._backend_type
    assert await fs._get_backend_mode(store, ctx) == "opengauss"
    store.get_account_backend.assert_not_awaited()
    store.get_keyword_search_info.assert_not_awaited()
