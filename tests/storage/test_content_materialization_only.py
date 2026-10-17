from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import (
    ContentBackfillConflictError,
    TextEmbeddingHandler,
    backfill_content,
    materialize_content_only,
)
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.viking_vector_index_backend import VIKINGDB_CONTENT_MAX_SIZE
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("acct", "user"), role=Role.USER)


@pytest.fixture
def fs(monkeypatch):
    fs = SimpleNamespace(read_file=AsyncMock(return_value="full source"))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.utils.embedding_utils._resolve_resource_content_type",
        AsyncMock(return_value=None),
    )
    return fs


def message(**fields):
    return EmbeddingMsg(
        message="embedding summary",
        context_data={
            "account_id": "acct",
            "uri": "viking://resources/source.txt",
            "level": 2,
            "context_type": "resource",
            **fields,
        },
    )


def make_online_store(*, mode, enabled, max_size=None):
    backend = SimpleNamespace(
        _mode=mode,
        _adapter=SimpleNamespace(USE_CONTENT_FIELD=enabled),
        get_content_policy=AsyncMock(
            return_value={
                "enabled": enabled,
                "max_size": max_size,
                "source": "full_source",
                "atomic_backfill": enabled,
            },
            side_effect=(
                None if mode == "opengauss"
                else AssertionError("legacy backend must use USE_CONTENT_FIELD")
            ),
        ),
    )
    store = SimpleNamespace(
        is_closing=False,
        get_account_backend=AsyncMock(side_effect=lambda account_id: {"acct": backend}[account_id]),
        get_content_policy=AsyncMock(side_effect=AssertionError("unexpected store policy lookup")),
        account_uses_content_field=AsyncMock(
            side_effect=AssertionError("worker must resolve account backend directly")
        ),
        upsert=AsyncMock(return_value="record"),
    )
    return store, backend


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["resource", "skill", "memory"])
async def test_full_source_does_not_change_embedding(ctx, fs, context_type):
    text = "正文" * (VIKINGDB_CONTENT_MAX_SIZE + 10)
    fs.read_file.return_value = text
    msg = message(context_type=context_type, is_leaf=True)
    assert await materialize_content_only(msg, ctx) == text
    assert msg.message == "embedding summary"
    expected_legacy = "embedding summary" if context_type == "memory" else text
    assert (
        await TextEmbeddingHandler._materialize_content(msg, ctx)
        == expected_legacy[:VIKINGDB_CONTENT_MAX_SIZE]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", ["", "prepared body"])
async def test_preloaded_body_remains_authoritative_and_consumed(ctx, fs, inline):
    msg = message(_materialized_content=inline)
    assert await materialize_content_only(msg, ctx) == inline
    assert "_materialized_content" not in msg.context_data
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("level,filename", [(0, ".abstract.md"), (1, ".overview.md")])
@pytest.mark.parametrize("sidecar_uri", [False, True])
async def test_summary_source_and_okf_body(ctx, fs, level, filename, sidecar_uri):
    root = "viking://resources/dir"
    source = root + "/" + filename
    fs.read_file.return_value = "---\ndirectory: viking://resources/dir/\n---\n\nsource summary\n"
    msg = message(uri=source if sidecar_uri else root, level=level)
    assert await materialize_content_only(msg, ctx) == "source summary"
    assert msg.message == "embedding summary"
    fs.read_file.assert_awaited_once_with(source, ctx=ctx)


@pytest.mark.asyncio
async def test_memory_preserves_full_source_frontmatter(ctx, fs):
    fs.read_file.return_value = "---\ntitle: source\n---\nbody and complete memory"
    assert (
        await materialize_content_only(message(context_type="memory"), ctx)
        == fs.read_file.return_value
    )


@pytest.mark.asyncio
async def test_missing_source_never_falls_back_to_summary(ctx, fs):
    fs.read_file.side_effect = FileNotFoundError("source gone")
    with pytest.raises(FileNotFoundError):
        await materialize_content_only(message(context_type="memory"), ctx)


@pytest.mark.asyncio
async def test_multimodal_fallback_is_abstract(ctx, fs):
    msg = EmbeddingMsg(
        message=[{"type": "image_url", "image_url": "image"}],
        context_data={
            "account_id": "acct",
            "abstract": "image summary",
        },
    )
    assert await materialize_content_only(msg, ctx) == "image summary"
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,max_size", [(True, None), (True, 7), (False, None)])
async def test_online_worker_obeys_account_policy_without_changing_embedding(
    ctx, fs, enabled, max_size
):
    from openviking.models.embedder.base import EmbedResult

    fs.read_file.return_value = "body" * (VIKINGDB_CONTENT_MAX_SIZE + 1)
    store, backend = make_online_store(mode="opengauss", enabled=enabled, max_size=max_size)
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
    )
    handler = TextEmbeddingHandler(store, SimpleNamespace(bind=Mock(return_value=embedder)))
    result = await handler.on_dequeue({"data": message().to_json()})
    assert result.outcome.value == "success"
    assert result.value == {"vector_written": True}
    written = store.upsert.call_args.args[0]
    if enabled:
        expected = fs.read_file.return_value
        assert written["content"] == (expected if max_size is None else expected[:max_size])
    else:
        assert "content" not in written
        fs.read_file.assert_not_awaited()
    assert embedder.embed_async.call_args.args[0] == "embedding summary"
    store.get_account_backend.assert_awaited_once_with(ctx.account_id)
    backend.get_content_policy.assert_awaited_once_with()
    store.get_content_policy.assert_not_called()
    store.account_uses_content_field.assert_not_called()


def make_store(record):
    return SimpleNamespace(
        get_content_policy=AsyncMock(return_value={"enabled": True, "max_size": None, "source": "full_source", "atomic_backfill": True}),
        get_strict=AsyncMock(return_value=[record]),
        backfill_content_record=AsyncMock(return_value=True),
        upsert=Mock(side_effect=AssertionError("must not insert")),
    )


@pytest.mark.asyncio
async def test_explicit_non_null_repair_keeps_snapshot_and_vectors(ctx, fs):
    record = {
        "id": "one",
        "account_id": "acct",
        "uri": "viking://resources/a.txt",
        "level": 2,
        "context_type": "memory",
        "content": "old summary",
        "updated_at": "old revision",
        "vector": [0.1, 0.2],
    }
    store = make_store(record)
    assert await backfill_content(store, ["one"], ctx=ctx) == {"requested": 1, "updated": 0}
    fs.read_file.assert_not_awaited()
    assert await backfill_content(store, ["one"], ctx=ctx, repair=True) == {
        "requested": 1,
        "updated": 1,
    }
    store.backfill_content_record.assert_awaited_once_with(
        record, "full source", ctx=ctx, repair=True
    )
    assert record["content"] == "old summary" and record["vector"] == [0.1, 0.2]
    store.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_cas_miss_and_missing_record_are_not_inserted(ctx, fs):
    record = {
        "id": "one",
        "account_id": "acct",
        "uri": "viking://resources/a.txt",
        "level": 2,
        "context_type": "memory",
        "content": None,
    }
    store = make_store(record)
    store.backfill_content_record.return_value = False
    with pytest.raises(ContentBackfillConflictError):
        await backfill_content(store, ["one"], ctx=ctx)
    assert store.get_strict.await_count == 2
    store.get_strict.return_value = []
    assert (await backfill_content(store, ["missing"], ctx=ctx))["updated"] == 0
    assert store.backfill_content_record.await_count == 1


@pytest.mark.asyncio
async def test_backfill_rejects_bad_scope_before_any_writes(ctx, fs):
    store = make_store({"id": "one", "account_id": "other"})
    with pytest.raises(PermissionError):
        await backfill_content(store, ["one"], ctx=ctx)
    with pytest.raises(ValueError, match="at most"):
        await backfill_content(store, ["one", "two"], max_records=1, ctx=ctx)
    store.backfill_content_record.assert_not_awaited()
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, False, 0, [], {}, object()])
@pytest.mark.parametrize(
    "fields",
    [
        {"level": 0},
        {"level": 1},
        {"context_type": "memory"},
        {"context_type": "resource"},
        {"context_type": "skill"},
    ],
)
async def test_invalid_source_is_never_fabricated_as_empty(ctx, fs, raw, fields):
    fs.read_file.return_value = raw
    with pytest.raises(TypeError):
        await materialize_content_only(message(**fields), ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", b""])
@pytest.mark.parametrize(
    "fields",
    [
        {"level": 0},
        {"level": 1},
        {"context_type": "memory"},
        {"context_type": "resource"},
        {"context_type": "skill"},
    ],
)
async def test_explicit_empty_source_is_valid(ctx, fs, raw, fields):
    fs.read_file.return_value = raw
    assert await materialize_content_only(message(**fields), ctx) == ""


@pytest.mark.asyncio
async def test_cas_false_confirmed_deletion_is_intentional_skip(ctx, fs):
    record = {
        "id": "one",
        "account_id": "acct",
        "uri": "viking://resources/a.txt",
        "level": 2,
        "context_type": "memory",
        "content": None,
    }
    store = make_store(record)
    store.backfill_content_record.return_value = False
    store.get_strict.side_effect = [[record], []]
    assert await backfill_content(store, ["one"], ctx=ctx) == {"requested": 1, "updated": 0}
    assert store.get_strict.await_count == 2


@pytest.mark.asyncio
async def test_cas_false_concurrent_non_null_is_explicit_conflict(ctx, fs):
    record = {
        "id": "one",
        "account_id": "acct",
        "uri": "viking://resources/a.txt",
        "level": 2,
        "context_type": "memory",
        "content": None,
    }
    store = make_store(record)
    store.backfill_content_record.return_value = False
    store.get_strict.side_effect = [[record], [{**record, "content": "concurrent writer"}]]
    with pytest.raises(ContentBackfillConflictError):
        await backfill_content(store, ["one"], ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,expected",
    [
        ('<!-- MEMORY_FIELDS {"internal": "hiddenmetadatatoken"} -->', ""),
        (
            'body <!-- MEMORY_FIELDS {"internal": "hiddenmetadatatoken"} -->\nmore',
            'body <!-- MEMORY_FIELDS {"internal": "hiddenmetadatatoken"} -->\nmore',
        ),
        (
            'body\n\n<!-- MEMORY_FIELDS {"internal": "hiddenmetadatatoken"} -->\n'
            '<!-- MEMORY_FIELDS {"second": "anotherhidden"} -->\n',
            "body",
        ),
        ("body <!-- ordinary comment -->", "body <!-- ordinary comment -->"),
    ],
)
async def test_memory_index_follows_reserved_trailer_grammar(ctx, fs, raw, expected):
    fs.read_file.return_value = raw
    msg = message(uri="viking://user/user/memories/preferences/grammar.md", context_type="memory")
    assert await materialize_content_only(msg, ctx) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("max_size", [None, 17, 100])
@pytest.mark.parametrize(
    "uri,context_type,is_memory",
    [
        ("viking://user/user/memories/preferences/example.md", "memory", True),
        ("viking://user/user/peers/peer/memories/preferences/example.md", "memory", True),
        ("viking://user/user/memories/preferences/example.md", "resource", True),
        ("viking://resources/example.txt", "memory", False),
        ("viking://user/user/skills/example/SKILL.md", "skill", False),
    ],
)
async def test_index_content_uses_public_visibility_before_backend_limit(
    ctx, fs, uri, context_type, is_memory, preloaded, max_size
):
    from openviking.session.memory.utils.content_visibility import visible_content

    body = "complete visible body visibletailtoken"
    raw = body + '\n\n<!-- MEMORY_FIELDS\n{"internal": "hiddenmetadatatoken"}\n-->'
    fs.read_file.return_value = raw
    fields = {"uri": uri, "context_type": context_type}
    if preloaded:
        fields["_materialized_content"] = raw
    msg = message(**fields)
    content = await materialize_content_only(msg, ctx, max_size=max_size)
    expected = visible_content(raw, uri=uri)
    assert expected == (body if is_memory else raw)
    assert content == (expected if max_size is None else expected[:max_size])
    if is_memory:
        assert "hiddenmetadatatoken" not in content and "MEMORY_FIELDS" not in content
    assert msg.message == "embedding summary"
    assert "_materialized_content" not in msg.context_data
    assert fs.read_file.await_count == (0 if preloaded else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("max_size", [None, 100])
async def test_online_memory_indexes_only_visible_body(ctx, fs, preloaded, max_size):
    from openviking.models.embedder.base import EmbedResult

    body = "memory complete visible tail uniquetailtoken"
    raw = body + '\n\n<!-- MEMORY_FIELDS\n{"internal": "hiddenmetadatatoken"}\n-->'
    fs.read_file.return_value = raw
    fields = {"uri": "viking://user/user/memories/preferences/online.md", "context_type": "memory"}
    if preloaded:
        fields["_materialized_content"] = raw
    msg = message(**fields)
    store, backend = make_online_store(mode="opengauss", enabled=True, max_size=max_size)
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
    )
    handler = TextEmbeddingHandler(store, SimpleNamespace(bind=Mock(return_value=embedder)))
    result = await handler.on_dequeue({"data": msg.to_json()})
    assert result.outcome.value == "success"
    assert result.value == {"vector_written": True}
    written = store.upsert.call_args.args[0]
    assert written["content"] == body
    assert "_materialized_content" not in written
    assert embedder.embed_async.call_args.args[0] == "embedding summary"
    store.get_account_backend.assert_awaited_once_with(ctx.account_id)
    backend.get_content_policy.assert_awaited_once_with()
    store.get_content_policy.assert_not_called()
    store.account_uses_content_field.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_memory_backfill_and_explicit_repair_remove_hidden_trailer(ctx, fs, repair):
    body = "full visible memory including tail uniquetailtoken"
    raw = body + '\n\n<!-- MEMORY_FIELDS\n{"internal": "hiddenmetadatatoken"}\n-->'
    fs.read_file.return_value = raw
    record = {
        "id": "one",
        "account_id": "acct",
        "uri": "viking://user/user/memories/preferences/repair.md",
        "level": 2,
        "context_type": "memory",
        "content": raw if repair else None,
        "updated_at": "revision",
        "vector": [0.1, 0.2],
    }
    store = make_store(record)
    assert await backfill_content(store, ["one"], ctx=ctx, repair=repair) == {
        "requested": 1,
        "updated": 1,
    }
    call = store.backfill_content_record.call_args
    assert call.args == (record, body)
    assert call.kwargs == ({"ctx": ctx, "repair": True} if repair else {"ctx": ctx})
    assert record["content"] == (raw if repair else None)
    assert record["vector"] == [0.1, 0.2]
    store.upsert.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["memory", "resource", "skill"])
@pytest.mark.parametrize("level,is_leaf", [(0, False), (1, False), (2, False), (2, True)])
@pytest.mark.parametrize("source_fails", [False, True])
async def test_legacy_materialization_matches_6006fc340(
    ctx, fs, context_type, level, is_leaf, source_fails
):
    source = "full source" * VIKINGDB_CONTENT_MAX_SIZE
    fs.read_file.return_value = source
    if source_fails:
        fs.read_file.side_effect = FileNotFoundError("source gone")
    msg = message(context_type=context_type, level=level, is_leaf=is_leaf)
    reads_source = is_leaf and context_type in {"resource", "skill"}
    if reads_source and source_fails:
        with pytest.raises(FileNotFoundError, match="source gone"):
            await TextEmbeddingHandler._materialize_content(msg, ctx)
    else:
        expected = source[:VIKINGDB_CONTENT_MAX_SIZE] if reads_source else "embedding summary"
        assert await TextEmbeddingHandler._materialize_content(msg, ctx) == expected
    assert fs.read_file.await_count == int(reads_source)
    assert msg.message == "embedding summary"


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", ["", "visible\n\n<!-- MEMORY_FIELDS {\"hidden\": true} -->"])
async def test_legacy_preloaded_memory_keeps_baseline_bytes(ctx, fs, inline):
    msg = message(
        uri="viking://user/user/memories/preferences/a.md",
        context_type="memory",
        _materialized_content=inline,
    )
    assert await TextEmbeddingHandler._materialize_content(msg, ctx) == inline
    assert "_materialized_content" not in msg.context_data
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("default_backend", ["local", "opengauss"])
@pytest.mark.parametrize(
    "account_backend,enabled",
    [
        ("local", False), ("http", False), ("volcengine", True), ("vikingdb", True),
        ("opengauss", False), ("opengauss", True),
    ],
)
@pytest.mark.parametrize("context_type,level,is_leaf", [
    ("memory", 2, True), ("resource", 0, False), ("skill", 1, False),
    ("resource", 2, True), ("skill", 2, True),
])
async def test_worker_uses_actual_backend_not_global_default_or_legacy_dynamic_lookup(
    monkeypatch, ctx, fs, default_backend, account_backend, enabled,
    context_type, level, is_leaf,
):
    from openviking.models.embedder.base import EmbedResult

    config = SimpleNamespace(
        storage=SimpleNamespace(vectordb=SimpleNamespace(backend=default_backend))
    )
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    tail = "actualaccountfullsourcetail"
    fs.read_file.return_value = "x" * (VIKINGDB_CONTENT_MAX_SIZE + 11) + tail
    store, backend = make_online_store(mode=account_backend, enabled=enabled)
    store._mode = default_backend
    store._shared_adapter = SimpleNamespace(USE_CONTENT_FIELD=not enabled)
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
    )
    msg = message(context_type=context_type, level=level, is_leaf=is_leaf)
    handler = TextEmbeddingHandler(store, SimpleNamespace(bind=lambda _: embedder))
    assert (await handler.on_dequeue({"data": msg.to_json()})).outcome.value == "success"
    store.upsert.assert_awaited_once()
    written = store.upsert.call_args.args[0]
    reads_source = enabled and (
        account_backend == "opengauss" or (is_leaf and context_type in {"resource", "skill"})
    )
    if enabled:
        expected = fs.read_file.return_value if reads_source else "embedding summary"
        if account_backend != "opengauss":
            expected = expected[:VIKINGDB_CONTENT_MAX_SIZE]
        assert written["content"] == expected
        assert (tail in written["content"]) is (account_backend == "opengauss")
    else:
        assert "content" not in written
    assert fs.read_file.await_count == int(reads_source)
    assert written["account_id"] == ctx.account_id
    assert written["vector"] == [0.1, 0.2]
    assert "_materialized_content" not in written
    embedder.embed_async.assert_awaited_once_with(msg.message, is_query=False)
    store.get_account_backend.assert_awaited_once_with(ctx.account_id)
    store.get_content_policy.assert_not_called()
    store.account_uses_content_field.assert_not_called()
    if account_backend == "opengauss":
        backend.get_content_policy.assert_awaited_once_with()
    else:
        backend.get_content_policy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("default_backend", ["local", "opengauss"])
@pytest.mark.parametrize(
    "account_backend,enabled",
    [
        ("local", False), ("http", False), ("volcengine", True), ("vikingdb", True),
        ("opengauss", False), ("opengauss", True),
    ],
)
async def test_account_uses_content_field_only_queries_policy_for_actual_opengauss(
    ctx, default_backend, account_backend, enabled
):
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend

    store, backend = make_online_store(mode=account_backend, enabled=enabled)
    store._mode = default_backend
    store._shared_adapter = SimpleNamespace(USE_CONTENT_FIELD=not enabled)
    if account_backend == "opengauss":
        backend._adapter.USE_CONTENT_FIELD = not enabled
    assert (
        await VikingVectorIndexBackend.account_uses_content_field(store, ctx.account_id)
    ) is enabled
    store.get_account_backend.assert_awaited_once_with(ctx.account_id)
    store.get_content_policy.assert_not_called()
    if account_backend == "opengauss":
        backend.get_content_policy.assert_awaited_once_with()
    else:
        backend.get_content_policy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [
    {"enabled": True, "source": "legacy", "atomic_backfill": False},
    {"enabled": True, "source": "full_source", "atomic_backfill": False},
    {"enabled": True, "source": "full_source", "atomic_backfill": MagicMock()},
    {"enabled": True, "atomic_backfill": True},
])
async def test_backfill_rejects_legacy_or_nonatomic_before_source_io(ctx, fs, policy):
    store = make_store({})
    store.get_content_policy.return_value = policy
    with pytest.raises(NotImplementedError, match="openGauss atomic"):
        await backfill_content(store, ["one"], ctx=ctx)
    store.get_strict.assert_not_awaited()
    store.backfill_content_record.assert_not_awaited()
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_policy_never_reads_or_changes_schema(ctx, fs):
    store = make_store({})
    store.get_content_policy.return_value = {"enabled": False, "max_size": None}
    with pytest.raises(NotImplementedError):
        await backfill_content(store, ["one"], ctx=ctx)
    store.get_strict.assert_not_awaited()
    store.backfill_content_record.assert_not_awaited()
    fs.read_file.assert_not_awaited()
