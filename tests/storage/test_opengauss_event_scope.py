from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "volcengine", "vikingdb", "opengauss"])
async def test_event_decay_scope_and_final_pagination(mode):
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade.acl_manager = None
    facade._get_backend_for_context = AsyncMock(return_value=SimpleNamespace(_mode=mode))
    facade.search = AsyncMock(side_effect=[
        [{"id": "normal", "_score": 0.8}], [{"id": "event", "_score": 0.9}],
    ])
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    rows = await facade.search_in_tenant(
        ctx, query_vector=[1.0], events_time_decay_protection="7d", request_now=now,
        target_directories=["viking://resources"], extra_filter=Eq("status", "ready"),
        level=[2], limit=1, offset=1,
    )
    assert [row["id"] for row in rows] == ["normal"]
    calls = facade.search.call_args_list
    assert "advance" not in calls[0].kwargs
    assert calls[1].kwargs["advance"] == {
        "time_decay": {"origin": now.isoformat(), "protection": "7d"}
    }
    scope = And([
        Eq("account_id", "account"),
        Or([PathScope("uri", "viking://resources", depth=-1)]),
        Eq("status", "ready"), In("level", [2]),
    ])
    assert calls[1].kwargs["filter"] == And([
        scope, Eq("search_tags", "memory_type=events"),
    ])
    assert calls[0].kwargs["filter"] == And([
        scope, RawDSL({"op": "must_not", "field": "search_tags", "conds": ["memory_type=events"]}),
    ])
    for call in calls:
        assert call.kwargs["limit"] == 2
        assert call.kwargs["offset"] == 0
        assert call.kwargs["ctx"] is ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "volcengine", "vikingdb", "opengauss"])
async def test_decay_backend_failure_is_not_hidden_or_replaced(mode):
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade.acl_manager = None
    facade._get_backend_for_context = AsyncMock(return_value=SimpleNamespace(_mode=mode))
    facade.search = AsyncMock(side_effect=NotImplementedError("advanced ranking unavailable"))
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    with pytest.raises(NotImplementedError, match="advanced ranking unavailable"):
        await facade.search_in_tenant(ctx, query_vector=[1.0], events_time_decay_protection="0")
