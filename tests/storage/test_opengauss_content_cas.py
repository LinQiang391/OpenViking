import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import backfill_content
from openviking.storage.expr import Eq
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier


def facade_and_snapshot():
    cursor = Mock(rowcount=1)
    collection = SimpleNamespace(_name="context", _cursor=lambda: cursor,
                                 _conn=Mock(), _array_fields=set(), _lock=threading.RLock())
    adapter = SimpleNamespace(retrieval_collection=lambda: collection,
                              _compile_filter=lambda value: {"op": "must", "field": "account_id", "conds": ["a"]},
                              _encode_uri_field_value=lambda value: value.replace("viking://", "/"))
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._get_backend_for_context = Mock(return_value=SimpleNamespace(_mode="opengauss", _adapter=adapter))
    facade._acl_enabled = AsyncMock(return_value=False)
    facade._tenant_filter = Mock(return_value=Eq("account_id", "a"))
    facade.acl_manager = None
    snapshot = {"id": "one", "account_id": "a", "uri": "viking://resources/a.txt", "updated_at": "2026-01-01T00:00:00Z"}
    return facade, snapshot, collection, cursor


@pytest.mark.asyncio
async def test_content_cas_is_versioned_tenant_bound_and_update_only():
    facade, snapshot, collection, cursor = facade_and_snapshot()
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    assert await facade.backfill_content_record(snapshot, "body", ctx=ctx)
    sql, params = cursor.execute.call_args.args
    assert "content IS NULL" in sql and "updated_at IS NOT DISTINCT" in sql and "uri=%s" in sql
    assert "INSERT" not in sql and "a" in params
    collection._conn.commit.assert_called_once()
    cursor.rowcount = 0
    assert not await facade.backfill_content_record(snapshot, "stale", ctx=ctx)


@pytest.mark.asyncio
async def test_content_cas_never_overwrites_existing_or_other_account():
    facade, snapshot, _, cursor = facade_and_snapshot()
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    snapshot["content"] = "already indexed"
    assert not await facade.backfill_content_record(snapshot, "replacement", ctx=ctx)
    cursor.execute.assert_not_called()
    snapshot["account_id"] = "b"
    with pytest.raises(PermissionError):
        await facade.backfill_content_record(snapshot, "replacement", ctx=ctx)


@pytest.mark.asyncio
async def test_upper_backfill_skips_non_null_content():
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    store = SimpleNamespace(
        get_content_policy=AsyncMock(return_value={"enabled": True, "max_size": None}),
        get_strict=AsyncMock(return_value=[{"id": "one", "account_id": "a", "content": "keep"}]),
        backfill_content_record=AsyncMock(),
    )
    assert await backfill_content(store, ["one"], ctx=ctx) == {"requested": 1, "updated": 0}
    store.backfill_content_record.assert_not_called()
