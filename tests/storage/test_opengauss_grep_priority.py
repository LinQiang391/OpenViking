# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OG shares legacy BM25 routing; only tag metadata enumeration is paginated."""

import threading
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.storage.expr import Eq, PathScope, RawDSL
from openviking.storage.vectordb.collection.result import SearchResult
from openviking.storage.vectordb_adapters import base as adapter_base
from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig
from tests.storage.test_grep_fulltext_backend_compat import (
    CLOUD,
    FILE,
    KEYWORDS,
    META,
    ROOT,
    TAG_FILTER,
    _assert_same_routes,
    _backend,
    _ctx,
    _facade,
    _filter_nodes,
    _fs,
    _trace_grep,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("global_mode", ["local", "vikingdb", "opengauss"])
@pytest.mark.parametrize("pattern", ["error", "ERR.*42", r"C\+\+", "", "   "],
                         ids=["word", "regex", "plus", "blank", "space"])
@pytest.mark.parametrize("node_limit", [None, 1], ids=["all", "one"])
async def test_bm25_success_never_extends_candidates_with_full_fs(global_mode, pattern, node_limit):
    backend = _backend("opengauss")
    store = _facade({"account": backend}, global_mode=global_mode)
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store)
    result = await fs.grep(ROOT, pattern, ctx=_ctx(), node_limit=node_limit,
                           case_insensitive=True, level_limit=4,
                           before_context=1, after_context=2)
    expected = {"error": 2, "ERR.*42": 1, r"C\+\+": 1, "": 4, "   ": 0}[pattern]
    assert result["count"] == result["match_count"] == (min(expected, 1) if node_limit else expected)
    assert result["files_scanned"] == 1
    assert store.get_account_backend.await_args_list == [call("account")] * 4
    kwargs = backend.search_by_keywords.await_args.kwargs
    assert kwargs["query"] == pattern.strip()
    assert kwargs["limit"] == (5 if node_limit else 100000)
    assert PathScope("uri", ROOT, depth=4) in _filter_nodes(kwargs["filter"])
    backend.get_collection_meta.assert_awaited_once_with(raise_on_error=False)
    backend.count.assert_not_awaited()
    backend.query.assert_not_awaited()
    fs._async_agfs.grep.assert_not_awaited()
    fs._async_agfs.ls.assert_not_awaited()
    fs._async_agfs.read.assert_awaited_once()


@pytest.mark.parametrize("pattern", [r"ERR.*42", r"C\+\+", r"\bneedle\b", "needle", "^$", "   "],
                         ids=["regex", "plus", "boundary", "word", "blank", "space"])
@pytest.mark.parametrize("candidates", [[], [{"uri": FILE}]], ids=["empty", "hit"])
@pytest.mark.parametrize("node_limit", [None, 10], ids=["all", "few"])
async def test_unindexed_file_and_unrecalled_long_tail_are_not_guaranteed(pattern, candidates, node_limit):
    from openviking.storage.viking_vector_index_backend import VIKINGDB_CONTENT_MAX_SIZE

    backend = _backend("opengauss")
    backend.search_by_keywords.return_value = candidates
    fs = _fs(_facade({"account": backend}))
    fs._async_agfs.grep.side_effect = NotImplementedError("native unavailable")
    fs._async_agfs.ls.return_value = [
        {"name": "a.md", "isDir": False}, {"name": "unindexed.md", "isDir": False},
    ]
    fs._async_agfs.read.return_value = (
        "x" * (VIKINGDB_CONTENT_MAX_SIZE + 10) + "\nbefore\nERR code 42 C++ needle\n\n   \nafter"
    ).encode()
    result = await fs.grep(ROOT, pattern, ctx=_ctx(), node_limit=node_limit,
                           before_context=1, after_context=1)
    assert {match["uri"] for match in result["matches"]} == ({FILE} if candidates else set())
    assert result["count"] == result["match_count"] == result["files_scanned"] == len(candidates)
    assert fs._async_agfs.read.await_count == len(candidates)
    assert all(len(m["before_context"]) == len(m["after_context"]) == 1 for m in result["matches"])
    backend.search_by_keywords.assert_awaited_once()
    fs._async_agfs.grep.assert_not_awaited()
    fs._async_agfs.ls.assert_not_awaited()


@pytest.mark.parametrize("candidates", [[], [{"uri": FILE, "search_tags": ["env=prod"]}]],
                         ids=["empty", "hit"])
@pytest.mark.parametrize("node_limit", [None, 3], ids=["all", "few"])
async def test_successful_bm25_does_not_enumerate_nonkeyword_tag_records(candidates, node_limit):
    backend = _backend("opengauss")
    backend.search_by_keywords.return_value = candidates
    extra = ROOT + "/unindexed.md"
    backend.query.return_value = [{"uri": extra, "search_tags": ["env=prod", "team=search"]}]
    fs = _fs(_facade({"account": backend}))
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=node_limit,
                           tag_filter=TAG_FILTER, allowed_uris={extra}, include_tags=True)
    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    backend.search_by_keywords.assert_awaited_once()
    backend.query.assert_not_awaited()
    fs._async_agfs.read.assert_not_awaited()
    fs._async_agfs.grep.assert_not_awaited()


@pytest.mark.parametrize("node_limit,expected,reads", [(2, 2, 1), (3, 3, 2), (None, 6, 3)],
                         ids=["full", "short", "all"])
async def test_duplicate_candidates_keep_legacy_order_and_counts(node_limit, expected, reads):
    runs = [await _trace_grep(mode, [{"uri": FILE, "search_tags": ["env=prod"]}] * 3,
                              options={"node_limit": node_limit, "tag_filter": TAG_FILTER,
                                       "include_tags": True, "before_context": 1, "after_context": 1})
            for mode in KEYWORDS]
    _assert_same_routes(runs)
    for outcome, _, fs, backend in runs:
        result = outcome[1]
        assert result["count"] == result["match_count"] == expected
        assert result["files_scanned"] == reads
        assert fs.read.await_count == reads
        assert [m["line"] for m in result["matches"]] == [1, 4, 1, 4, 1, 4][:expected]
        assert all(m["tags"] == ["env=prod"] for m in result["matches"])
        assert result["matches"][0]["after_context"] == [{"line": 2, "content": "ERR code 42"}]
        backend.query.assert_not_awaited()
        fs._grep_fs.assert_not_awaited()


async def test_candidate_path_depth_exclusion_and_allowlist_match_legacy_not_extra_og_checks():
    excluded = ROOT + "/excluded"
    outside, deep, denied = ROOT + "-other/a.md", ROOT + "/nested/deep.md", ROOT + "/denied.md"
    candidates = [{"uri": u} for u in (outside, deep, excluded + "/a.md", denied, FILE)]
    runs = [await _trace_grep(mode, candidates, "needle", {
        "node_limit": 10, "level_limit": 1, "exclude_uri": excluded + "/",
        "allowed_uris": {outside, deep, excluded + "/a.md", FILE},
    }, body=b"needle") for mode in KEYWORDS]
    _assert_same_routes(runs)
    for outcome, trace, fs, _ in runs:
        assert [m["uri"] for m in outcome[1]["matches"]] == [deep, FILE]
        assert fs._grep_in_files.await_args.args[0] == [outside, deep, FILE]
        assert fs.read.await_args_list == [call(uri, ctx=_ctx()) for uri in (outside, deep, FILE)]
        kwargs = next(kwargs for name, _, kwargs in trace if name == "bm25")
        nodes = list(_filter_nodes(kwargs["filter"]))
        assert PathScope("uri", ROOT, depth=1) in nodes
        assert RawDSL({"op": "must_not", "field": "uri", "conds": [excluded], "para": "-d=-1"}) in nodes


@pytest.mark.parametrize("node_limit,remote_limit,expected", [
    (-5, -25, 1), (0, 100000, 2), (None, 100000, 2), (1, 5, 1), (3, 15, 2), (25000, 100000, 2),
], ids=["neg", "zero", "all", "one", "short", "cap"])
async def test_budget_keeps_legacy_negative_zero_and_insufficient_behavior(node_limit, remote_limit, expected):
    backend = _backend("opengauss")
    fs = _fs(_facade({"account": backend}))
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=node_limit)
    assert result["count"] == result["match_count"] == expected
    assert result["files_scanned"] == 1
    assert backend.search_by_keywords.await_args.kwargs["limit"] == remote_limit
    fs._async_agfs.grep.assert_not_awaited()
    fs._async_agfs.ls.assert_not_awaited()


@pytest.mark.parametrize("candidate_match", [False, True], ids=["miss", "hit"])
async def test_native_fs_is_not_called_after_successful_insufficient_bm25(candidate_match):
    backend = _backend("opengauss")
    fs = _fs(_facade({"account": backend}))
    fs._async_agfs.read.return_value = b"needle" if candidate_match else b"no hit"
    fs._async_agfs.grep.return_value = {
        "matches": [{"file": "unindexed.md", "line": 8, "content": "needle"}], "files_scanned": 17,
    }
    result = await fs.grep(ROOT, "needle", ctx=_ctx(), node_limit=2,
                           before_context=1, after_context=1)
    assert result["count"] == result["match_count"] == int(candidate_match)
    assert result["files_scanned"] == 1
    assert all(m["uri"] == FILE for m in result["matches"])
    fs._async_agfs.grep.assert_not_awaited()


@pytest.mark.parametrize("metadata", [[], [{"uri": FILE, "search_tags": ["env=prod"]}]],
                         ids=["empty", "hit"])
@pytest.mark.parametrize("node_limit", [None, 3], ids=["all", "few"])
async def test_include_tags_does_not_turn_empty_recall_into_fs_scan(metadata, node_limit):
    backend = _backend("opengauss")
    backend.search_by_keywords.return_value = []
    backend.query.return_value = metadata
    fs = _fs(_facade({"account": backend}))
    result = await fs.grep(ROOT, "needle", ctx=_ctx(), node_limit=node_limit, include_tags=True)
    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    backend.query.assert_not_awaited()
    fs._async_agfs.grep.assert_not_awaited()


@pytest.mark.parametrize("global_mode", ["local", "vikingdb", "opengauss"])
async def test_shared_fs_resolves_actual_account_with_baseline_lookup_budget(global_mode):
    backends = {mode: _backend(mode) for mode in (*CLOUD, "opengauss", "local")}
    store = _facade(backends, global_mode=global_mode)
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store)
    for mode in ("opengauss", "vikingdb", "local", "volcengine", "opengauss"):
        backend = backends[mode]
        backend.search_by_keywords.reset_mock()
        store.get_account_backend.reset_mock()
        fs._async_agfs.grep.reset_mock()
        metadata_cached = backend.get_collection_meta.await_count > 0
        result = await fs.grep(ROOT, "error", ctx=_ctx(mode), node_limit=1)
        expected_lookups = 1 if mode == "local" else 3 if metadata_cached else 4
        assert store.get_account_backend.await_args_list == [call(mode)] * expected_lookups
        assert backend.get_collection_meta.await_count == int(mode != "local")
        assert backend.search_by_keywords.await_count == int(mode != "local")
        assert fs._async_agfs.grep.await_count == int(mode == "local")
        backend.count.assert_not_awaited()
        assert result["count"] == int(mode != "local")


@pytest.fixture
def scalar_chain(monkeypatch):
    random = Mock(side_effect=AssertionError("random dense query is not a scalar scan"))
    monkeypatch.setattr(adapter_base.random, "uniform", random)

    def make(pages):
        cursor = Mock()
        cursor.description = [(name,) for name in ("id", "uri", "search_tags", "_scalar_val")]
        cursor.fetchall.side_effect = pages
        collection = OpenGaussCollection.__new__(OpenGaussCollection)
        collection._name = "context"
        collection._conn = Mock()
        collection._conn.cursor.return_value = cursor
        collection._lock = threading.RLock()
        collection._field_names = {"id", "uri", "search_tags", "account_id"}
        collection._array_fields = {"search_tags"}
        collection._date_time_fields = set()
        collection._indexes = {}
        collection._pending_indexes = {}
        collection.has_index = Mock(return_value=False)
        collection.search_by_vector = Mock(side_effect=RuntimeError("ANN index missing"))
        collection.search_by_random = Mock(side_effect=AssertionError("no random scan"))

        def scalar(**kwargs):
            result = OpenGaussCollection.search_by_scalar(collection, **kwargs)
            assert isinstance(result, SearchResult)
            return result

        collection.search_by_scalar = Mock(wraps=scalar)
        adapter = OpenGaussCollectionAdapter.__new__(OpenGaussCollectionAdapter)
        adapter._collection = collection
        adapter._collection_name = "context"
        adapter._index_name = "default"
        adapter._dimension = 2
        adapter._distributed = False
        adapter.query = Mock(wraps=adapter.query)
        backend = _SingleAccountBackend(
            VectorDBBackendConfig(backend="local", name="context", dimension=2),
            bound_account_id="account", shared_adapter=adapter,
        )
        backend.get_collection_meta = AsyncMock(return_value=META)
        backend.search_by_keywords = AsyncMock(side_effect=RuntimeError("explicit BM25 failure"))
        backend.count = AsyncMock(side_effect=AssertionError("no count probe"))
        store = _facade({"account": backend}, global_mode="vikingdb")
        store.filter = AsyncMock(wraps=store.filter)
        return store, backend, adapter, collection, cursor, random

    return make


@pytest.mark.parametrize("node_limit", [None, 0, -5, 3], ids=["all", "zero", "neg", "few"])
@pytest.mark.parametrize("route", ["exception", "direct"], ids=["fail", "fs"])
async def test_scalar_pages(scalar_chain, node_limit, route):
    extra = ROOT + "/unindexed.md"
    # Only cursor rows are virtual; the facade, account query, adapter and scalar SQL run.
    store, backend, adapter, collection, cursor, random = scalar_chain([
        ((f"{i:06}", "/resources/a.md", ["env=prod"], f"{i:06}") for i in range(100000)),
        [("100000", "/resources/unindexed.md", ["env=prod", "second-page"], "100000")],
    ])
    if route == "direct":
        backend.get_collection_meta.return_value = {**META, "FullText": []}
    fs = _fs(store)
    fs._grep_in_files = AsyncMock(wraps=fs._grep_in_files)
    fs._grep_fs = AsyncMock(wraps=fs._grep_fs)
    fs._async_agfs.ls.return_value = [{"name": "unindexed.md", "isDir": False}]
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=node_limit,
                           tag_filter=TAG_FILTER, include_tags=True)
    expected = 2 if not node_limit else 1 if node_limit < 0 else 2
    assert result["count"] == result["match_count"] == expected
    assert result["files_scanned"] == 1
    assert {m["uri"] for m in result["matches"]} == {extra}
    assert all(m["tags"] == ["env=prod", "second-page"] for m in result["matches"])
    assert [entry.kwargs["offset"] for entry in store.filter.await_args_list] == [0, 100000]
    assert [entry.kwargs["offset"] for entry in adapter.query.call_args_list] == [0, 100000]
    for entry in adapter.query.call_args_list:
        kwargs = entry.kwargs
        assert kwargs["order_by"] == "id" and kwargs["order_desc"] is False
        assert kwargs["limit"] == 100000
        assert kwargs["query_vector"] is kwargs["sparse_query_vector"] is None
        nodes = list(_filter_nodes(kwargs["filter"]))
        assert Eq("account_id", "account") in nodes
        assert PathScope("uri", ROOT, depth=10) in nodes
        assert RawDSL(TAG_FILTER) in nodes
    for entry in collection.search_by_scalar.call_args_list:
        kwargs = entry.kwargs
        assert kwargs["field"] == "id" and kwargs["order"] == "asc"
        assert kwargs["index_name"] == "default"
        assert kwargs["output_fields"] == ["uri", "search_tags"]
    assert [entry.kwargs["offset"] for entry in collection.search_by_scalar.call_args_list] == [0, 100000]
    assert cursor.execute.call_count == cursor.fetchall.call_count == cursor.close.call_count == 2
    for offset, entry in zip((0, 100000), cursor.execute.call_args_list, strict=True):
        sql, params = entry.args
        assert 'ORDER BY "id" ASC, id ASC' in sql
        assert params[-2:] == [100000, offset]
        assert "account" in params and "env=prod" in params
    assert collection._conn.commit.call_count == 2
    collection._conn.rollback.assert_not_called()
    assert collection._indexes == collection._pending_indexes == {}
    collection.has_index.assert_not_called()
    collection.search_by_vector.assert_not_called()
    collection.search_by_random.assert_not_called()
    random.assert_not_called()
    fs._grep_fs.assert_awaited_once()
    fallback = fs._grep_fs.await_args.kwargs
    assert fallback["allowed_uris"] == {FILE, extra}
    assert fallback["node_limit"] == node_limit
    assert "raise_on_permission_error" not in fallback
    fs._grep_in_files.assert_not_awaited()
    assert backend.search_by_keywords.await_count == int(route == "exception")
    backend.get_collection_meta.assert_awaited_once_with(raise_on_error=False)
    backend.count.assert_not_awaited()


@pytest.mark.parametrize("route", ["exception", "direct"], ids=["fail", "fs"])
@pytest.mark.parametrize("scope", ["tag", "allow", "empty", "tags"])
async def test_scalar_fallback_conditions(scalar_chain, route, scope):
    pages = [[]] if scope == "empty" else [[("a", "/resources/a.md", ["env=prod"], "a")]]
    store, backend, adapter, collection, cursor, random = scalar_chain(pages)
    if route == "direct":
        backend.get_collection_meta.return_value = {**META, "FullText": []}
    fs = _fs(store)
    fs._grep_fs = AsyncMock(wraps=fs._grep_fs)
    options = {"include_tags": True}
    if scope != "tags":
        options["tag_filter"] = TAG_FILTER
    if scope == "allow":
        options["allowed_uris"] = {ROOT + "/unindexed.md"}
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=3,
                           level_limit=1, exclude_uri=ROOT + "/excluded/",
                           before_context=1, after_context=1, **options)
    enumerates = scope in {"tag", "empty"} or (route == "direct" and scope == "allow")
    assert adapter.query.call_count == cursor.execute.call_count == int(enumerates)
    if enumerates:
        kwargs = adapter.query.call_args.kwargs
        assert kwargs["order_by"] == "id" and kwargs["order_desc"] is False
        nodes = list(_filter_nodes(kwargs["filter"]))
        assert Eq("account_id", "account") in nodes
        assert PathScope("uri", ROOT, depth=1) in nodes
        assert RawDSL(TAG_FILTER) in nodes
        # Baseline fallback metadata scope never gained the BM25 exclusion clause.
        assert not any(isinstance(node, RawDSL) and node == RawDSL({
            "op": "must_not", "field": "uri", "conds": [ROOT + "/excluded"], "para": "-d=-1",
        }) for node in nodes)
    assert result["count"] == (2 if scope == "tag" or (route == "direct" and scope == "allow") else
                               0 if scope in {"allow", "empty"} else 0)
    if scope == "empty":
        fs._grep_fs.assert_not_awaited()
    else:
        fs._grep_fs.assert_awaited_once()
        assert "raise_on_permission_error" not in fs._grep_fs.await_args.kwargs
    collection.search_by_vector.assert_not_called()
    collection.search_by_random.assert_not_called()
    random.assert_not_called()


@pytest.mark.parametrize("threshold", [0, 1, 10001], ids=["zero", "one", "high"])
@pytest.mark.parametrize("metadata_state", ["error", "invalid", "no_fulltext", "no_content"])
async def test_og_resolution_requires_fulltext_before_threshold(threshold, metadata_state):
    backend = _backend("opengauss")
    if metadata_state == "error":
        backend.get_collection_meta.side_effect = RuntimeError("metadata down")
    else:
        backend.get_collection_meta.return_value = {
            "invalid": {}, "no_fulltext": {**META, "FullText": []}, "no_content": {**META, "Fields": []},
        }[metadata_state]
    backend.count.side_effect = AssertionError("metadata failure must precede count")
    store = _facade({"account": backend})
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store, threshold=threshold)
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=1)
    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 9}
    fs._async_agfs.grep.assert_awaited_once()
    fs._async_agfs.read.assert_not_awaited()
    backend.search_by_keywords.assert_not_awaited()
    backend.get_collection_meta.assert_awaited_once_with(raise_on_error=False)
    backend.count.assert_not_awaited()
    assert store.get_account_backend.await_args_list == [call("account")] * 3
    assert not fs._count_cache
    if metadata_state in ("error", "invalid"):
        assert not fs._fulltext_available
    else:
        supported, expires_at = fs._fulltext_available[("account", "opengauss", "context", "default")]
        assert supported is False and expires_at is not None


@pytest.mark.parametrize("threshold", [0, 1, 10000, 10001], ids=["zero", "one", "equal", "high"])
@pytest.mark.parametrize("count_fails", [False, True], ids=["ok", "fail"])
async def test_og_resolution_obeys_threshold_and_count_failure_fallback(threshold, count_fails):
    backend = _backend("opengauss")
    if count_fails:
        backend.count.side_effect = RuntimeError("count down")
    store = _facade({"account": backend})
    store.get_account_backend = AsyncMock(wraps=store.get_account_backend)
    fs = _fs(store, threshold=threshold)
    result = await fs.grep(ROOT, "error", ctx=_ctx(), node_limit=1)
    remote = threshold == 0 or (not count_fails and threshold <= 10000)
    assert result["count"] == result["match_count"] == int(remote)
    assert result["files_scanned"] == (1 if remote else 9)
    assert backend.search_by_keywords.await_count == int(remote)
    assert fs._async_agfs.read.await_count == int(remote)
    assert fs._async_agfs.grep.await_count == int(not remote)
    backend.get_collection_meta.assert_awaited_once_with(raise_on_error=False)
    assert backend.count.await_count == int(threshold != 0)
    assert store.get_account_backend.await_args_list == [call("account")] * (
        3 + int(threshold != 0) + int(remote)
    )
    assert list(fs._fulltext_available.values()) == [(True, None)]
    assert bool(fs._count_cache) == (threshold != 0 and not count_fails)
