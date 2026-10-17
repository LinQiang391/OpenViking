from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, PathScope, RawDSL
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.grep_config import GrepConfig

ROOT = "viking://resources"
FILE = ROOT + "/a"
EMPTY = {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
READY = {"ready": True, "engine": "bm25", "candidate_complete": False}


async def _keyword_info(*, ctx):
    return dict(READY)


async def _search_by_keywords(*, query, mode, fields, filter, limit, output_fields, ctx):
    return [{"uri": FILE}]


@pytest.fixture
def priority_fs():
    fs = VikingFS(
        agfs=SimpleNamespace(),
        grep_config=GrepConfig(engine="auto", switch_to_remote_threshold=0),
    )
    store = SimpleNamespace(
        _mode="opengauss",
        get_account_backend=AsyncMock(return_value=SimpleNamespace(_mode="opengauss")),
        get_keyword_search_info=AsyncMock(spec=_keyword_info, side_effect=_keyword_info),
        search_by_keywords=AsyncMock(spec=_search_by_keywords, side_effect=_search_by_keywords),
        filter=AsyncMock(return_value=[{"uri": FILE, "search_tags": ["env=prod"]}]),
    )
    fs._get_vector_store = lambda: store
    fs._ensure_access = AsyncMock()
    fs.stat = AsyncMock(return_value={"isDir": True})
    fs._grep_in_files = AsyncMock(
        return_value={
            "count": 1,
            "match_count": 1,
            "files_scanned": 1,
            "matches": [{"uri": FILE, "line": 1, "content": "word"}],
        }
    )
    fs._grep_fs = AsyncMock(return_value=dict(EMPTY))
    fs._get_cached_count = AsyncMock(return_value=100000)
    return fs, store


async def _priority(fs, pattern="word", limit=1, ctx=None, threshold=0, **kwargs):
    return await fs._grep_keyword_priority(
        ROOT, pattern, None, False, limit, 10, ctx, threshold, **kwargs
    )


@pytest.mark.asyncio
async def test_priority_only_shortcuts_a_satisfied_limit(priority_fs):
    fs, store = priority_fs
    assert (await _priority(fs))["count"] == 1
    assert await _priority(fs, limit=2) is None
    store.search_by_keywords.assert_awaited_with(
        query="word",
        mode="bm25",
        fields=["content"],
        filter=PathScope("uri", ROOT, depth=10),
        limit=10,
        output_fields=["uri"],
        ctx=None,
    )


@pytest.mark.asyncio
async def test_empty_candidates_never_prove_no_match(priority_fs):
    fs, store = priority_fs
    store.search_by_keywords.side_effect = None
    store.search_by_keywords.return_value = []
    assert await _priority(fs) is None
    fs._grep_in_files.assert_not_awaited()
    await fs.grep(ROOT, "word", node_limit=1)
    fs._grep_fs.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", [r"foo.*bar", "foo|bar", r"\bword\b", "[", "", "   "])
async def test_complex_regex_retains_full_fs_semantics(priority_fs, pattern):
    fs, store = priority_fs
    assert await _priority(fs, pattern=pattern) is None
    store.search_by_keywords.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, 0, -1])
async def test_unbounded_or_nonpositive_limit_cannot_shortcut(priority_fs, limit):
    fs, store = priority_fs
    assert await _priority(fs, limit=limit) is None
    store.get_keyword_search_info.assert_not_awaited()
    store.search_by_keywords.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info",
    [
        {**READY, "ready": False},
        {**READY, "candidate_complete": True},
        {"ready": True},
    ],
)
async def test_priority_requires_explicit_ready_incomplete_capability(priority_fs, info):
    fs, store = priority_fs
    store.get_keyword_search_info.side_effect = None
    store.get_keyword_search_info.return_value = info
    assert await _priority(fs) is None
    store.search_by_keywords.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_is_fresh_after_index_removed(priority_fs):
    fs, store = priority_fs
    store.get_keyword_search_info.side_effect = [READY, {**READY, "ready": False}]
    assert (await _priority(fs))["count"] == 1
    assert await _priority(fs) is None
    assert store.get_keyword_search_info.await_count == 2
    assert store.search_by_keywords.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["get_keyword_search_info", "search_by_keywords", "read"])
async def test_priority_errors_fall_back_without_trusting_candidates(priority_fs, stage):
    fs, store = priority_fs
    if stage == "read":
        fs._grep_in_files.side_effect = RuntimeError("read failed")
    else:
        getattr(store, stage).side_effect = RuntimeError("index unavailable")
    assert await _priority(fs) is None


@pytest.mark.asyncio
async def test_priority_honors_volume_threshold_and_candidate_cap(priority_fs):
    fs, store = priority_fs
    fs._get_cached_count.return_value = 5
    assert await _priority(fs, threshold=10) is None
    store.search_by_keywords.assert_not_awaited()
    fs._get_cached_count.return_value = 10
    await _priority(fs, limit=1000, threshold=10)
    assert store.search_by_keywords.await_args.kwargs["limit"] == 1000


@pytest.mark.asyncio
async def test_priority_deduplicates_and_excludes_outside_scope(priority_fs):
    fs, store = priority_fs
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    excluded = ROOT + "/archive"
    store.search_by_keywords.side_effect = None
    store.search_by_keywords.return_value = [
        {"uri": FILE},
        {"uri": FILE},
        {},
        {"uri": excluded},
        {"uri": excluded + "/old"},
        {"uri": ROOT + "-other/secret"},
        {"uri": "viking://other/secret"},
    ]
    result = await fs._grep_keyword_priority(ROOT, "word", excluded + "/", True, 1, 3, ctx, 0, 2, 4)
    assert result["count"] == 1
    fs._ensure_access.assert_awaited_once_with(excluded, ctx)
    store.get_keyword_search_info.assert_awaited_once_with(ctx=ctx)
    assert store.search_by_keywords.await_args.kwargs["ctx"] is ctx
    assert store.search_by_keywords.await_args.kwargs["filter"] == PathScope("uri", ROOT, depth=3)
    fs._grep_in_files.assert_awaited_once_with([FILE], "word", True, 1, ctx, 2, 4)


@pytest.mark.asyncio
async def test_exclude_acl_denial_is_not_swallowed(priority_fs):
    fs, store = priority_fs
    fs._ensure_access.side_effect = PermissionDeniedError("access denied")
    with pytest.raises(PermissionDeniedError):
        await fs._grep_keyword_priority(ROOT, "word", ROOT + "/private", False, 1, 10, None, 0)
    store.get_keyword_search_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_reads_keep_acl_and_context_matching(priority_fs):
    fs, _ = priority_fs
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    fs._grep_in_files = VikingFS._grep_in_files.__get__(fs)
    fs.read = AsyncMock(return_value=b"before\nWORD\nafter")
    result = await fs._grep_keyword_priority(ROOT, "word", None, True, 1, 10, ctx, 0, 1, 1)
    assert result["matches"] == [
        {
            "uri": FILE,
            "line": 2,
            "content": "WORD",
            "before_context": [{"line": 1, "content": "before"}],
            "after_context": [{"line": 3, "content": "after"}],
        }
    ]
    fs.read.assert_awaited_once_with(FILE, ctx=ctx)
    fs.read.side_effect = PermissionDeniedError("read access revoked")
    assert await _priority(fs, ctx=ctx) is None


@pytest.mark.asyncio
async def test_opengauss_resolver_uses_live_capabilities_not_facade_mode_or_cache(priority_fs):
    fs, store = priority_fs
    fs._collection_has_fulltext = AsyncMock(side_effect=AssertionError("must not cache readiness"))
    store._backend_type = "not-a-known-backend"
    store.get_keyword_search_info.side_effect = [
        {**READY, "candidate_complete": True},
        {**READY, "candidate_complete": True, "ready": False},
        READY,
    ]
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    assert await fs._resolve_grep_engine("auto", ROOT, ctx, 0) == "vikingdb_then_fs"
    assert await fs._resolve_grep_engine("auto", ROOT, ctx, 0) == "fs"
    assert await fs._resolve_grep_engine("auto", ROOT, ctx, 0) == "fs"
    assert store.get_account_backend.await_count == 3
    assert store.get_keyword_search_info.await_count == 3
    fs._collection_has_fulltext.assert_not_awaited()
    fs._get_cached_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_capability_still_obeys_threshold(priority_fs):
    fs, store = priority_fs
    store.get_keyword_search_info.side_effect = None
    store.get_keyword_search_info.return_value = {**READY, "candidate_complete": True}
    fs._get_cached_count.return_value = 9
    assert await fs._resolve_grep_engine("auto", ROOT, None, 10) == "fs"
    fs._get_cached_count.return_value = 10
    assert await fs._resolve_grep_engine("auto", ROOT, None, 10) == "vikingdb_then_fs"


@pytest.mark.asyncio
async def test_public_grep_priority_and_unsatisfied_fallback(priority_fs):
    fs, store = priority_fs
    result = await fs.grep(ROOT, "word", node_limit=1, before_context=2, after_context=3)
    assert result["count"] == 1
    fs._ensure_access.assert_awaited_once_with(ROOT, None)
    fs.stat.assert_awaited_once_with(ROOT, ctx=None, skip_count=True)
    fs._grep_in_files.assert_awaited_once_with([FILE], "word", False, 1, None, 2, 3)
    fs._grep_fs.assert_not_awaited()
    assert store.get_keyword_search_info.await_count == 2
    assert await fs.grep(ROOT, "word", node_limit=2) == EMPTY
    fs._grep_fs.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"allowed_uris": set()},
        {"allowed_uris": {FILE}},
        {"include_tags": True},
        {"tag_filter": {"op": "must", "field": "search_tags", "conds": ["env=prod"]}},
        {"content_transform": lambda text, uri: text},
    ],
)
async def test_priority_does_not_bypass_mainline_filters(priority_fs, options):
    fs, store = priority_fs
    await fs.grep(ROOT, "word", node_limit=1, **options)
    store.search_by_keywords.assert_not_awaited()
    fs._grep_fs.assert_awaited_once()
    for name in ("allowed_uris", "content_transform"):
        if name in options:
            assert fs._grep_fs.await_args.kwargs[name] == options[name]
    if "tag_filter" in options:
        store.filter.assert_awaited_once_with(
            filter=And([PathScope("uri", ROOT, depth=10), RawDSL(options["tag_filter"])]),
            limit=100000,
            output_fields=["uri", "search_tags"],
            ctx=None,
        )
        assert fs._grep_fs.await_args.kwargs["allowed_uris"] == {FILE}


@pytest.mark.asyncio
@pytest.mark.parametrize("uri", ["viking://user/alice/sessions/s1", "viking://session/s1"])
async def test_session_never_uses_lexical_candidates(priority_fs, uri):
    fs, store = priority_fs
    await fs.grep(uri, "word", node_limit=1)
    store.get_keyword_search_info.assert_not_awaited()
    store.search_by_keywords.assert_not_awaited()
    fs._grep_fs.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_public_complex_regex_never_relies_on_keyword_recall(priority_fs, complete):
    fs, store = priority_fs
    store.get_keyword_search_info.side_effect = None
    store.get_keyword_search_info.return_value = {**READY, "candidate_complete": complete}
    await fs.grep(ROOT, r"foo.*bar", node_limit=1)
    store.search_by_keywords.assert_not_awaited()
    fs._grep_fs.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_unready_keywords_fall_back_to_fs(priority_fs):
    fs, store = priority_fs
    store.get_keyword_search_info.side_effect = None
    store.get_keyword_search_info.return_value = {**READY, "ready": False}
    await fs.grep(ROOT, "word", node_limit=1)
    store.search_by_keywords.assert_not_awaited()
    fs._grep_fs.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolver_propagates_request_context_for_each_account(priority_fs):
    fs, store = priority_fs
    first = RequestContext(user=UserIdentifier("first", "user"), role=Role.USER)
    second = RequestContext(user=UserIdentifier("second", "user"), role=Role.USER)
    store.get_keyword_search_info.side_effect = [
        {**READY, "candidate_complete": True},
        {**READY, "ready": False},
    ]
    assert await fs._resolve_grep_engine("auto", ROOT, first, 0) == "vikingdb_then_fs"
    assert await fs._resolve_grep_engine("auto", ROOT, second, 0) == "fs"
    assert [call.kwargs["ctx"] for call in store.get_keyword_search_info.await_args_list] == [
        first,
        second,
    ]


@pytest.mark.asyncio
async def test_forced_fs_does_not_probe_keywords(priority_fs):
    fs, store = priority_fs
    fs.grep_config = GrepConfig(engine="fs", switch_to_remote_threshold=0)
    await fs.grep(ROOT, "word", node_limit=1)
    store.get_keyword_search_info.assert_not_awaited()
    store.search_by_keywords.assert_not_awaited()
    fs._grep_fs.assert_awaited_once()
