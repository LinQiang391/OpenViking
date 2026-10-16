from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import Eq
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_event_decay_scope_and_final_pagination():
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._acl_enabled = AsyncMock(return_value=True)
    facade._build_scope_filter = Mock(return_value=Eq("account_id", "account"))
    facade._get_backend_for_context = Mock(return_value=SimpleNamespace(_mode="opengauss"))
    facade.search = AsyncMock(side_effect=[
        [{"id": "normal", "_score": 0.8}], [{"id": "event", "_score": 0.9}],
    ])
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    rows = await facade.search_in_tenant(ctx, query_vector=[1.0], events_time_decay_protection="7d", request_now=now, limit=1, offset=1)
    assert [row["id"] for row in rows] == ["normal"]
    calls = facade.search.call_args_list
    assert "advance" not in calls[0].kwargs
    assert calls[1].kwargs["advance"]["time_decay"]["origin"] is now
    assert "memory_type=events" in str(calls[1].kwargs["filter"])
    assert "context_type" in str(calls[1].kwargs["filter"])
    for call in calls:
        assert call.kwargs["limit"] == 2
        assert "account" in str(call.kwargs["filter"])


@pytest.mark.asyncio
async def test_unsupported_backend_does_not_silently_ignore_decay():
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._acl_enabled = AsyncMock(return_value=False)
    facade._build_scope_filter = Mock(return_value=None)
    facade._get_backend_for_context = Mock(return_value=SimpleNamespace(_mode="local"))
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    with pytest.raises(NotImplementedError, match="openGauss"):
        await facade.search_in_tenant(ctx, query_vector=[1.0], events_time_decay_protection="0")
