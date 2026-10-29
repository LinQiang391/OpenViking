# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Standard embedding writes retain the shared, bounded content contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.viking_vector_index_backend import (
    VIKINGDB_CONTENT_MAX_SIZE,
    VikingVectorIndexBackend,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("acct", "user"), role=Role.USER)


@pytest.fixture
def fs(monkeypatch):
    fs = SimpleNamespace(read_file=AsyncMock(return_value="正文" * VIKINGDB_CONTENT_MAX_SIZE))
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
            "account_id": "acct", "uri": "viking://resources/source.txt",
            "level": 2, "is_leaf": True, "context_type": "resource", **fields,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["resource", "skill", "memory"])
async def test_standard_materializer_preserves_source_selection_and_cap(ctx, fs, context_type):
    msg = message(context_type=context_type)
    expected = "embedding summary" if context_type == "memory" else fs.read_file.return_value
    assert await TextEmbeddingHandler._materialize_content(msg, ctx) == expected[:VIKINGDB_CONTENT_MAX_SIZE]
    assert msg.message == "embedding summary"
    if context_type == "memory":
        fs.read_file.assert_not_awaited()
    else:
        fs.read_file.assert_awaited_once_with(msg.context_data["uri"], ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", ["", "prepared body", "x" * (VIKINGDB_CONTENT_MAX_SIZE + 1)])
async def test_preloaded_content_is_authoritative_consumed_and_bounded(ctx, fs, inline):
    msg = message(_materialized_content=inline)
    assert await TextEmbeddingHandler._materialize_content(msg, ctx) == inline[:VIKINGDB_CONTENT_MAX_SIZE]
    assert "_materialized_content" not in msg.context_data
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1])
async def test_summary_records_use_embedding_message_without_source_io(ctx, fs, level):
    msg = message(level=level, is_leaf=False)
    assert await TextEmbeddingHandler._materialize_content(msg, ctx) == msg.message
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_multimodal_content_uses_bounded_abstract(ctx, fs):
    msg = EmbeddingMsg(message=[{"type": "image_url", "image_url": "image"}],
                       context_data={"account_id": ctx.account_id, "abstract": "x" * (VIKINGDB_CONTENT_MAX_SIZE + 1)})
    assert await TextEmbeddingHandler._materialize_content(msg, ctx) == "x" * VIKINGDB_CONTENT_MAX_SIZE
    fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_text_source_is_not_replaced_with_summary(ctx, fs):
    fs.read_file.side_effect = FileNotFoundError("source gone")
    with pytest.raises(FileNotFoundError):
        await TextEmbeddingHandler._materialize_content(message(), ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "volcengine", "vikingdb", "opengauss"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_embedding_worker_uses_actual_account_content_flag_and_legacy_cap(ctx, fs, mode, enabled):
    backend = SimpleNamespace(_mode=mode, _adapter=SimpleNamespace(USE_CONTENT_FIELD=enabled))
    store = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    store._closing = False
    store._backend_type = "local" if mode != "local" else "opengauss"
    store.get_account_backend = AsyncMock(side_effect=lambda account: {"acct": backend}[account])
    store.upsert = AsyncMock(return_value="record")
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
    )
    provider = SimpleNamespace(bind=Mock(return_value=embedder))
    outcome = await TextEmbeddingHandler(store, provider).on_dequeue({"data": message().to_json()})
    assert outcome.outcome is ProcessOutcome.SUCCESS
    assert outcome.value == {"vector_written": True}
    written = store.upsert.await_args.args[0]
    if enabled:
        assert written["content"] == fs.read_file.return_value[:VIKINGDB_CONTENT_MAX_SIZE]
        fs.read_file.assert_awaited_once()
    else:
        assert "content" not in written
        fs.read_file.assert_not_awaited()
    assert "_materialized_content" not in written
    assert written["vector"] == [0.1, 0.2]
    embedder.embed_async.assert_awaited_once()
    assert embedder.embed_async.await_args.args[0] == "embedding summary"
    store.get_account_backend.assert_awaited_once_with(ctx.account_id)
    assert store.upsert.await_args.kwargs["ctx"].account_id == ctx.account_id
