import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclAction
from openviking.storage.collection_schemas import backfill_content
from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def facade_and_snapshot():
    adapter = SimpleNamespace(
        mode="opengauss", backfill_content_record=Mock(return_value=True),
    )
    backend = _SingleAccountBackend(
        VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="a", shared_adapter=adapter,
    )
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._get_backend_for_context = AsyncMock(return_value=backend)
    facade.acl_manager = None
    snapshot = {
        "id": "one", "account_id": "a", "uri": "viking://resources/a.txt",
        "updated_at": "2026-01-01T00:00:00Z", "content": None,
    }
    return facade, snapshot, adapter


@pytest.mark.asyncio
async def test_content_cas_passes_snapshot_and_write_scope_to_atomic_adapter():
    facade, snapshot, adapter = facade_and_snapshot()
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    assert await facade.backfill_content_record(snapshot, "body", ctx=ctx)
    call = adapter.backfill_content_record.call_args
    assert call.args == (snapshot, "body")
    assert call.kwargs["repair"] is False
    from openviking.storage.expr import Eq

    assert call.kwargs["filter"] == facade._merge_filters(
        Eq("account_id", "a"), facade._tenant_filter(ctx, acl_enabled=False, action=AclAction.WRITE),
    )
    adapter.backfill_content_record.return_value = False
    assert not await facade.backfill_content_record(snapshot, "stale", ctx=ctx)


@pytest.mark.asyncio
async def test_content_cas_never_overwrites_existing_or_other_account_without_explicit_repair():
    facade, snapshot, adapter = facade_and_snapshot()
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    snapshot["content"] = "already indexed"
    assert not await facade.backfill_content_record(snapshot, "replacement", ctx=ctx)
    adapter.backfill_content_record.assert_not_called()
    assert await facade.backfill_content_record(snapshot, "replacement", ctx=ctx, repair=True)
    assert adapter.backfill_content_record.call_args.kwargs["repair"] is True
    assert adapter.backfill_content_record.call_args.args[0]["content"] == "already indexed"
    snapshot["account_id"] = "b"
    with pytest.raises(PermissionError):
        await facade.backfill_content_record(snapshot, "replacement", ctx=ctx, repair=True)
    assert adapter.backfill_content_record.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "uri", "updated_at"])
async def test_content_cas_requires_a_source_version(field):
    facade, snapshot, adapter = facade_and_snapshot()
    snapshot.pop(field)
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    with pytest.raises(InvalidArgumentError):
        await facade.backfill_content_record(snapshot, "body", ctx=ctx)
    adapter.backfill_content_record.assert_not_called()


@pytest.mark.asyncio
async def test_content_cas_uses_write_not_read_acl_grants():
    facade, snapshot, adapter = facade_and_snapshot()
    facade.acl_manager = SimpleNamespace(is_enabled=AsyncMock(return_value=True))
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    await facade.backfill_content_record(snapshot, "body", ctx=ctx)
    from openviking.storage.expr import Eq

    expected = facade._merge_filters(
        Eq("account_id", "a"), facade._tenant_filter(ctx, acl_enabled=True, action=AclAction.WRITE),
    )
    assert adapter.backfill_content_record.call_args.kwargs["filter"] == expected
    assert expected != facade._merge_filters(
        Eq("account_id", "a"), facade._tenant_filter(ctx, acl_enabled=True),
    )


@pytest.mark.asyncio
async def test_content_cas_errors_propagate_without_upsert():
    facade, snapshot, adapter = facade_and_snapshot()
    adapter.backfill_content_record.side_effect = RuntimeError("transaction rolled back")
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    with pytest.raises(RuntimeError, match="transaction rolled back"):
        await facade.backfill_content_record(snapshot, "body", ctx=ctx)


def test_real_adapter_content_cas_is_single_transaction_and_never_upserts():
    cursor = Mock(rowcount=1)
    collection = SimpleNamespace(
        _name="context", _get_all_columns=lambda: ["id", "content", "_ov_content_empty"],
        _array_fields=set(), _lock=threading.RLock(), _cursor=lambda: cursor,
        _conn=Mock(), _content_write_state=Mock(return_value={}), _check_content_coverage=Mock(),
    )
    adapter = OpenGaussCollectionAdapter.__new__(OpenGaussCollectionAdapter)
    adapter.retrieval_collection = lambda: collection
    snapshot = {
        "id": "one", "account_id": "a", "uri": "viking://resources/a.txt",
        "updated_at": "2026-01-01T00:00:00Z", "content": "original",
    }
    assert not adapter.backfill_content_record(snapshot, "body")
    cursor.execute.assert_not_called()
    assert adapter.backfill_content_record(snapshot, "body", repair=True)
    sql, params = cursor.execute.call_args.args
    assert "UPDATE" in sql and "INSERT" not in sql
    assert "id=%s AND uri=%s AND account_id=%s" in sql
    assert "updated_at IS NOT DISTINCT FROM %s" in sql
    assert "content IS NOT DISTINCT FROM %s" in sql
    assert "original" in params and "a" in params
    collection._conn.commit.assert_called_once()
    cursor.close.assert_called_once()
    cursor.execute.side_effect = RuntimeError("database failure")
    with pytest.raises(RuntimeError, match="database failure"):
        adapter.backfill_content_record(snapshot, "body", repair=True)
    collection._conn.rollback.assert_called_once()
    assert collection._conn.commit.call_count == 1
    cursor.execute.side_effect = None
    cursor.rowcount = 0
    assert not adapter.backfill_content_record(snapshot, "stale", repair=True)


@pytest.mark.asyncio
async def test_upper_backfill_skips_non_null_content():
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    store = SimpleNamespace(
        get_content_policy=AsyncMock(return_value={
            "enabled": True, "max_size": None, "source": "full_source", "atomic_backfill": True,
        }),
        supports_atomic_content_backfill=AsyncMock(return_value=True),
        get_strict=AsyncMock(return_value=[{"id": "one", "account_id": "a", "content": "keep"}]),
        backfill_content_record=AsyncMock(),
    )
    assert await backfill_content(store, ["one"], ctx=ctx) == {"requested": 1, "updated": 0}
    store.backfill_content_record.assert_not_called()
