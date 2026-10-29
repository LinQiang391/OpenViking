# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Baseline grep routing, metadata caching and actual-account dispatch."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.pyagfs.exceptions import AGFSPermissionDeniedError
from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool
from openviking.storage.expr import And, PathScope, RawDSL
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_fs import _grep as grep_module
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.grep_config import GrepConfig

ROOT = "viking://resources"
FILE = ROOT + "/a.md"
TAG_FILTER = {"op": "must", "field": "search_tags", "conds": ["env=prod"]}
META = {
    "Fields": [{"FieldName": "content", "FieldType": "text"}],
    "FullText": [{"Field": "content"}],
}
CLOUD = ["volcengine", "vikingdb"]
KEYWORDS = [*CLOUD, "opengauss"]
pytestmark = pytest.mark.asyncio


def _ctx(account="account"):
    return RequestContext(user=UserIdentifier(account, "user"), role=Role.USER)


def _backend(mode):
    return SimpleNamespace(
        _mode=mode, collection_name="context", index_name="default",
        get_collection_meta=AsyncMock(return_value=META),
        search_by_keywords=AsyncMock(return_value=[{"uri": FILE, "search_tags": ["env=prod"]}]),
        query=AsyncMock(return_value=[{"uri": FILE, "search_tags": ["env=prod"]}]),
        count=AsyncMock(return_value=10000),
    )


def _facade(backends, global_mode="opengauss"):
    # Only adapter construction is bypassed; account resolution and proxies remain real.
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
    fs = VikingFS(agfs=SimpleNamespace(), vector_store=store,
                  grep_config=GrepConfig(engine=engine, switch_to_remote_threshold=threshold))
    fs._async_agfs.stat = AsyncMock(return_value={"name": "resources", "isDir": True})
    fs._async_agfs.read = AsyncMock(return_value=b"error\nERR code 42\nC++\nerror again")
    fs._async_agfs.grep = AsyncMock(return_value={"matches": [], "files_scanned": 9})
    fs._async_agfs.ls = AsyncMock(return_value=[{"name": "a.md", "isDir": False}])
    return fs


def _filter_nodes(expr):
    yield expr
    if isinstance(expr, And):
        for child in expr.conds:
            yield from _filter_nodes(child)


@pytest.mark.parametrize("mode", KEYWORDS)
async def test_positive_fulltext_cache_preserves_baseline_metadata_semantics(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    service = SearchService(fs)
    await service.ensure_keywords_search_supported(_ctx())
    backend.get_collection_meta.side_effect = RuntimeError("metadata unavailable")
    await service.ensure_keywords_search_supported(_ctx())
    backend.get_collection_meta.assert_awaited_once_with(raise_on_error=True)
    assert list(fs._fulltext_available.values()) == [(True, None)]


@pytest.mark.parametrize("mode", KEYWORDS)
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


@pytest.mark.parametrize("mode", KEYWORDS)
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


@pytest.mark.parametrize("mode", KEYWORDS)
async def test_strict_metadata_failure_propagates_without_caching(mode):
    backend = _backend(mode)
    backend.get_collection_meta.side_effect = RuntimeError("metadata unavailable")
    fs = _fs(_facade({"account": backend}))
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx())
    assert not fs._fulltext_available
    fallback_engine = "opengauss_fs" if mode == "opengauss" else "fs"
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 0) == fallback_engine
    assert backend.get_collection_meta.await_count == 2
    assert not fs._fulltext_available


@pytest.mark.parametrize("node_limit", [None, 1], ids=["unlimited", "bounded"])
@pytest.mark.parametrize("reason", ["no_fulltext", "below_threshold", "count_error"])
async def test_og_direct_fs_tag_enumeration_is_ordered(reason, node_limit):
    backend = _backend("opengauss")
    if reason == "no_fulltext":
        backend.get_collection_meta.return_value = {**META, "FullText": []}
    if reason == "count_error":
        backend.count.side_effect = RuntimeError("count unavailable")
    fs = _fs(_facade({"account": backend}), threshold=10001)
    fs._async_agfs.grep.side_effect = NotImplementedError()
    result = await fs.grep(ROOT, "error", ctx=_ctx(), tag_filter=TAG_FILTER,
                           node_limit=node_limit, allowed_uris={FILE})
    assert result["count"] == (1 if node_limit else 2)
    assert all(match["uri"] == FILE and match["tags"] == ["env=prod"]
               for match in result["matches"])
    kwargs = backend.query.await_args.kwargs
    assert kwargs["order_by"] == "id" and kwargs["order_desc"] is False
    assert kwargs["offset"] == 0
    backend.search_by_keywords.assert_not_awaited()


async def test_cache_identity_stays_scoped_by_account_collection_and_index():
    first, second = _backend("opengauss"), _backend("vikingdb")
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
async def test_supported_modes_are_checked_before_cache_or_metadata(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    fs._fulltext_available[("account", mode, "context", "default")] = (True, None)
    assert not await fs._collection_has_fulltext(
        fs.vector_store, _ctx(), supported_modes=("volcengine",), raise_on_error=True,
    )
    backend.get_collection_meta.assert_not_awaited()


@pytest.mark.parametrize("global_mode", ["local", "vikingdb", "opengauss"])
async def test_keyword_support_selects_actual_account_not_global_mode(global_mode):
    backends = {mode: _backend(mode) for mode in ["local", *KEYWORDS]}
    store = _facade(backends, global_mode=global_mode)
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    service = SearchService(_fs(store))
    for mode, backend in backends.items():
        store.get_account_backend.reset_mock()
        if mode == "local":
            with pytest.raises(InvalidArgumentError, match="not supported"):
                await service.ensure_keywords_search_supported(_ctx(mode))
            backend.get_collection_meta.assert_not_awaited()
            assert store.get_account_backend.await_args_list == [call(mode)]
        else:
            await service.ensure_keywords_search_supported(_ctx(mode))
            backend.get_collection_meta.assert_awaited_once_with(raise_on_error=True)
            assert store.get_account_backend.await_args_list == [call(mode), call(mode)]


@pytest.mark.parametrize("mode", KEYWORDS)
@pytest.mark.parametrize("pattern", ["error", "ERR.*42", "C++", "", "   "],
                         ids=["word", "regex", "plus", "blank", "space"])
@pytest.mark.parametrize("node_limit", [None, 1])
@pytest.mark.parametrize("tags", [False, True])
async def test_cloud_public_grep_preserves_exact_recall_and_tag_routes(mode, pattern, node_limit, tags):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    options = {"tag_filter": TAG_FILTER, "include_tags": True} if tags else {}
    result = await fs.grep(ROOT, pattern, ctx=_ctx(), node_limit=node_limit, **options)
    kwargs = backend.search_by_keywords.await_args.kwargs
    assert kwargs["query"] == pattern.strip()
    assert kwargs["limit"] == (5 if node_limit else 100000)
    assert kwargs["output_fields"] == (["uri", "search_tags"] if tags else ["uri"])
    assert kwargs["mode"] is None and kwargs["fields"] is None
    assert PathScope("uri", ROOT, depth=10) in _filter_nodes(kwargs["filter"])
    if tags:
        assert RawDSL(TAG_FILTER) in _filter_nodes(kwargs["filter"])
        assert all(match["tags"] == ["env=prod"] for match in result["matches"])
    expected = {"error": 2, "ERR.*42": 1, "C++": 1, "": 4, "   ": 0}[pattern]
    assert result["count"] == (min(expected, node_limit) if node_limit else expected)
    fs._async_agfs.grep.assert_not_awaited()
    backend.query.assert_not_awaited()


@pytest.mark.parametrize("mode", KEYWORDS)
async def test_resolver_keeps_count_cache_threshold_zero_and_failure_behavior(mode):
    backend = _backend(mode)
    fs = _fs(_facade({"account": backend}))
    remote_engine = "opengauss_then_fs" if mode == "opengauss" else "vikingdb_then_fs"
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 0) == remote_engine
    backend.count.assert_not_awaited()
    fallback_engine = "opengauss_fs" if mode == "opengauss" else "fs"
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 10001) == fallback_engine
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 10000) == remote_engine
    assert backend.count.await_count == 1
    backend.get_collection_meta.assert_awaited_once_with(raise_on_error=False)
    fs._count_cache.clear()
    backend.count.side_effect = RuntimeError("count failed")
    assert await fs._resolve_grep_engine("auto", ROOT, _ctx(), 1) == fallback_engine


@pytest.mark.parametrize("mode", ["local", *KEYWORDS])
@pytest.mark.parametrize("route", ["fs", "projection", "session", "force_fs"])
async def test_explicit_fs_routes_never_probe_backend(mode, route):
    backend = _backend(mode)
    store = _facade({"account": backend})
    store.get_account_backend = AsyncMock(side_effect=AssertionError("must not resolve backend"))
    fs = _fs(store, engine="fs" if route == "fs" else "auto")
    fs._get_vector_store = Mock(side_effect=AssertionError("must not access vector store"))
    options = {"content_transform": lambda text, uri: text} if route == "projection" else {}
    if route == "force_fs":
        options["force_fs"] = True
    uri = "viking://user/user/sessions/s1" if route == "session" else ROOT
    await fs.grep(uri, "error", ctx=_ctx(), node_limit=1, **options)
    fs._get_vector_store.assert_not_called()
    store.get_account_backend.assert_not_awaited()
    backend.get_collection_meta.assert_not_awaited()
    backend.search_by_keywords.assert_not_awaited()


@pytest.mark.parametrize("global_mode", ["local", "vikingdb", "opengauss"])
@pytest.mark.parametrize("mode", ["local", *KEYWORDS])
async def test_filter_only_find_never_adds_keyword_preflight(mode, global_mode):
    backend = _backend(mode)
    backend.get_collection_meta.side_effect = AssertionError("must not add find preflight")
    store = _facade({"account": backend}, global_mode=global_mode)
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    service = SearchService(_fs(store))
    service.ensure_keywords_search_supported = AsyncMock(side_effect=AssertionError("no preflight"))
    result = await service.find("", _ctx(), target_uri=ROOT, filter=TAG_FILTER, search_type="keywords")
    assert result.total == 1
    backend.query.assert_awaited_once()
    store.get_account_backend.assert_awaited_once_with("account")
    service.ensure_keywords_search_supported.assert_not_awaited()
    backend.get_collection_meta.assert_not_awaited()


async def _trace_grep(mode, candidates, pattern="error", options=None, *,
                      bm25_error=None, read_error=None, query_error=None,
                      native_error=None, listing_error=None, threshold=0,
                      metadata=META, count_error=None, body=None):
    backend = _backend(mode)
    backend.get_collection_meta.return_value = metadata
    backend.search_by_keywords.return_value = deepcopy(candidates)
    backend.search_by_keywords.side_effect = bm25_error
    backend.query.side_effect = query_error
    backend.count.side_effect = count_error
    store = _facade({"account": backend})
    fs = _fs(store, threshold=threshold)
    if body is not None:
        fs._async_agfs.read.return_value = body
    fs._async_agfs.grep.side_effect = native_error
    fs._async_agfs.ls.side_effect = listing_error
    if read_error is not None:
        fs.read = AsyncMock(side_effect=read_error)
    trace = []

    def watch(owner, attr, name):
        original = getattr(owner, attr)

        async def record(*args, **kwargs):
            trace.append((name, deepcopy(args), deepcopy(kwargs)))
            return await original(*args, **kwargs)

        setattr(owner, attr, AsyncMock(side_effect=record))

    for owner, attr, name in [
        (backend, "get_collection_meta", "meta"),
        (backend, "count", "count"),
        (store, "search_by_keywords", "bm25"),
        (store, "filter", "filter"),
        (fs, "_grep_in_files", "candidates"),
        (fs, "_grep_fs", "fs"),
        (fs, "read", "read"),
        (fs, "ls", "ls"),
        (fs._async_agfs, "grep", "native"),
    ]:
        watch(owner, attr, name)
    try:
        outcome = ("result", await fs.grep(ROOT, pattern, ctx=_ctx(), **(options or {})))
    except Exception as exc:
        outcome = ("error", type(exc), str(exc))
    return outcome, trace, fs, backend


def _assert_same_routes(runs, *, ordered_metadata=False):
    baseline_result, baseline_trace, _, _ = runs[0]
    for index, (result, trace, _, _) in enumerate(runs):
        assert result == baseline_result
        if ordered_metadata and KEYWORDS[index] == "opengauss":
            normalized = []
            for name, args, kwargs in trace:
                kwargs = dict(kwargs)
                if name == "filter":
                    assert kwargs.pop("order_by") == "id"
                    assert kwargs.pop("order_desc") is False
                    assert kwargs.pop("offset") == 0
                normalized.append((name, args, kwargs))
            assert normalized == baseline_trace
        else:
            assert trace == baseline_trace


_LIMITS = [None, 0, -5, 1, 3]
_LIMIT_IDS = ["all", "zero", "neg", "one", "few"]
_CANDIDATES = [[], [{"uri": FILE, "search_tags": ["env=prod"]}],
               [{"uri": FILE, "search_tags": ["env=prod"]},
                {"uri": ROOT + "/b.md", "search_tags": ["team=search"]}]]
_OPTIONS = [
    {},
    {"include_tags": True},
    {"tag_filter": TAG_FILTER},
    {"tag_filter": TAG_FILTER, "include_tags": True, "allowed_uris": {FILE}},
    {"allowed_uris": set()},
    {"case_insensitive": True, "before_context": 1, "after_context": 2,
     "exclude_uri": ROOT + "/excluded/", "level_limit": 1},
]


@pytest.mark.parametrize("node_limit", _LIMITS, ids=_LIMIT_IDS)
@pytest.mark.parametrize("candidates", _CANDIDATES, ids=["empty", "short", "full"])
@pytest.mark.parametrize("pattern", ["error", "错误", "ERR.*42", r"C\+\+", "[", "", "   ",
                                     " error | 错误 "],
                         ids=["word", "zh", "regex", "escape", "bad", "blank", "space", "or"])
@pytest.mark.parametrize("options", _OPTIONS, ids=["plain", "tags", "tag", "allow", "deny", "ctx"])
async def test_same_candidates_have_identical_routes_and_entire_results(
    node_limit, candidates, pattern, options
):
    runs = [await _trace_grep(mode, candidates, pattern, {**options, "node_limit": node_limit},
                              body="error\n错误\nERR code 42\nC++\nerror again".encode())
            for mode in KEYWORDS]
    _assert_same_routes(runs)
    for outcome, trace, fs, backend in runs:
        fs._grep_fs.assert_not_awaited()
        fs._async_agfs.grep.assert_not_awaited()
        fs.ls.assert_not_awaited()
        backend.query.assert_not_awaited()
        request = next(kwargs for name, _, kwargs in trace if name == "bm25")
        assert request["limit"] == (min(node_limit * 5, 100000) if node_limit else 100000)
        assert request["query"] == " ".join(p.strip() for p in pattern.split("|") if p.strip())
        assert request["ctx"] == _ctx()
        assert request["output_fields"] == (
            ["uri", "search_tags"] if options.get("tag_filter") or options.get("include_tags")
            else ["uri"]
        )
        if pattern == "[" and fs._grep_in_files.await_count:
            assert outcome[0:2] == ("error", InvalidArgumentError)
        elif not fs._grep_in_files.await_count:
            assert outcome == ("result", {"matches": [], "count": 0,
                                           "match_count": 0, "files_scanned": 0})


_ERRORS = [RuntimeError, NotImplementedError, PermissionDeniedError, PermissionError,
           AGFSPermissionDeniedError]
_ERROR_IDS = ["runtime", "unsupported", "denied", "perm", "agfsperm"]


@pytest.mark.parametrize("error_type", _ERRORS, ids=_ERROR_IDS)
@pytest.mark.parametrize("options", _OPTIONS, ids=["plain", "tags", "tag", "allow", "deny", "ctx"])
@pytest.mark.parametrize("node_limit", _LIMITS, ids=_LIMIT_IDS)
async def test_bm25_exceptions_use_legacy_fallback(error_type, options, node_limit):
    runs = [await _trace_grep(mode, _CANDIDATES[1], options={**options, "node_limit": node_limit},
                              bm25_error=error_type("offline")) for mode in KEYWORDS]
    _assert_same_routes(runs, ordered_metadata=True)
    for outcome, _, fs, backend in runs:
        assert outcome[0] == "result"
        fs._grep_in_files.assert_not_awaited()
        fs._grep_fs.assert_awaited_once()
        assert "raise_on_permission_error" not in fs._grep_fs.await_args.kwargs
        assert backend.query.await_count == int(
            options.get("tag_filter") is not None and options.get("allowed_uris") is None
        )


@pytest.mark.parametrize("error_type", _ERRORS + [FileNotFoundError, UnicodeError],
                         ids=_ERROR_IDS + ["missing", "decode"])
@pytest.mark.parametrize("node_limit", _LIMITS, ids=_LIMIT_IDS)
async def test_candidate_read_errors_are_skipped_without_full_fs(error_type, node_limit):
    runs = [await _trace_grep(mode, _CANDIDATES[1], options={"node_limit": node_limit},
                              read_error=error_type("unreadable")) for mode in KEYWORDS]
    _assert_same_routes(runs)
    assert runs[0][0] == ("result", {"matches": [], "count": 0,
                                     "match_count": 0, "files_scanned": 1})
    for _, _, fs, _ in runs:
        fs._grep_fs.assert_not_awaited()
        fs.read.assert_awaited_once_with(FILE, ctx=_ctx())
        assert "raise_on_permission_error" not in fs._grep_in_files.await_args.kwargs


@pytest.mark.parametrize("error_type", _ERRORS, ids=_ERROR_IDS)
@pytest.mark.parametrize("stage", ["filter", "native", "listing", "read"])
async def test_fallback_errors_keep_legacy_outcomes(error_type, stage):
    options = {"tag_filter": TAG_FILTER} if stage == "filter" else {}
    runs = [await _trace_grep(
        mode, _CANDIDATES[1], options=options, bm25_error=RuntimeError("bm25 offline"),
        query_error=error_type("failed") if stage == "filter" else None,
        native_error=error_type("failed") if stage == "native" else NotImplementedError(),
        listing_error=error_type("failed") if stage == "listing" else None,
        read_error=error_type("failed") if stage == "read" else None,
    ) for mode in KEYWORDS]
    _assert_same_routes(runs, ordered_metadata=True)
    if stage == "filter":
        assert runs[0][0] == ("result", {"matches": [], "count": 0,
                                        "match_count": 0, "files_scanned": 0})
        for _, _, fs, _ in runs:
            fs._grep_fs.assert_not_awaited()


@pytest.mark.parametrize("reason", ["no_fulltext", "below", "count_error"])
@pytest.mark.parametrize("options", _OPTIONS, ids=["plain", "tags", "tag", "allow", "deny", "ctx"])
async def test_direct_fs_alias_has_legacy_conditions_and_kwargs(reason, options):
    runs = []
    for mode in KEYWORDS:
        # A high threshold and the default count exercise the same FS decision.
        run = await _trace_grep(
            mode, [], options=options, threshold=10001,
            metadata={**META, "FullText": []} if reason == "no_fulltext" else META,
            count_error=RuntimeError("count unavailable") if reason == "count_error" else None,
        )
        runs.append(run)
    _assert_same_routes(runs, ordered_metadata=True)
    for _, _, fs, backend in runs:
        fs.vector_store.search_by_keywords.assert_not_awaited()
        assert backend.query.await_count == int(options.get("tag_filter") is not None)


@pytest.mark.parametrize("mode", KEYWORDS)
async def test_child_listing_denial_keeps_legacy_skip(mode):
    backend = _backend(mode)
    backend.search_by_keywords.side_effect = RuntimeError("explicit BM25 failure")
    fs = _fs(_facade({"account": backend}))
    fs._async_agfs.grep.side_effect = NotImplementedError()
    fs.ls = AsyncMock(side_effect=[
        [{"name": "child", "isDir": True}], PermissionDeniedError("child denied"),
    ])
    result = await fs.grep(ROOT, "error", ctx=_ctx())
    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    assert fs.ls.await_count == 2
    fs._async_agfs.read.assert_not_awaited()


@pytest.mark.parametrize("mode", KEYWORDS)
@pytest.mark.parametrize("route", ["exception", "direct"], ids=["fail", "fs"])
async def test_only_og_metadata_enumeration_continues_past_one_page(mode, route):
    backend = _backend(mode)
    if route == "direct":
        backend.get_collection_meta.return_value = {**META, "FullText": []}
    else:
        backend.search_by_keywords.side_effect = RuntimeError("explicit BM25 failure")
    first = [{"uri": FILE, "search_tags": ["env=prod"]}] * 100000
    second = [{"uri": ROOT + "/page2.md", "search_tags": ["env=prod", "page2"]}]
    backend.query.side_effect = [first, second]
    store = _facade({"account": backend})
    store.filter = AsyncMock(wraps=store.filter)
    fs = _fs(store)
    fs._async_agfs.ls.return_value = [{"name": "page2.md", "isDir": False}]
    result = await fs.grep(ROOT, "error", ctx=_ctx(), tag_filter=TAG_FILTER)
    assert backend.query.await_count == (2 if mode == "opengauss" else 1)
    requests = [entry.kwargs for entry in store.filter.await_args_list]
    if mode == "opengauss":
        assert [entry["offset"] for entry in requests] == [0, 100000]
        assert all(entry["order_by"] == "id" and entry["order_desc"] is False for entry in requests)
        assert result["count"] == 2
        assert all(m["tags"] == ["env=prod", "page2"] for m in result["matches"])
    else:
        assert set(requests[0]) == {"filter", "limit", "output_fields", "ctx"}
        assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    assert all(entry["limit"] == 100000 for entry in requests)


async def test_top_level_direct_fs_tag_allowlist_keeps_legacy_overwrite():
    runs = [await _trace_grep(mode, [], options={"tag_filter": TAG_FILTER,
                                                "allowed_uris": {ROOT + "/other.md"}},
                              metadata={**META, "FullText": []}) for mode in KEYWORDS]
    _assert_same_routes(runs, ordered_metadata=True)
    for outcome, _, fs, _ in runs:
        assert outcome[1]["count"] == 2
        assert fs._grep_fs.await_args.kwargs["allowed_uris"] == {FILE}
        assert all(m["uri"] == FILE for m in outcome[1]["matches"])
