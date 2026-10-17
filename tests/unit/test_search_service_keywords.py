from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking_cli.session.user_id import UserIdentifier


def _ctx():
    return RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)


@pytest.mark.asyncio
async def test_keywords_capability_check_propagates_metadata_failures():
    error = RuntimeError("metadata unavailable")
    check = AsyncMock(side_effect=error)
    vector_store = object()
    fs = SimpleNamespace(
        _get_vector_store=lambda: vector_store,
        _collection_has_fulltext=check,
    )

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await SearchService(fs).ensure_keywords_search_supported(_ctx())

    check.assert_awaited_once_with(
        vector_store,
        _ctx(),
        supported_modes=("volcengine", "vikingdb", "opengauss"),
        raise_on_error=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_keyword_find_delegates_without_backend_preflight(fails):
    result = object()
    find = AsyncMock(return_value=result)
    if fails:
        find.side_effect = NotImplementedError("Keyword search index is not ready")
    get_store = Mock(side_effect=AssertionError("find must not inspect the backend"))
    fs = SimpleNamespace(find=find, _get_vector_store=get_store)
    service = SearchService(fs)
    service.ensure_keywords_search_supported = AsyncMock(
        side_effect=AssertionError("find must not add a capability check")
    )
    ctx = _ctx()
    if fails:
        with pytest.raises(NotImplementedError, match="not ready"):
            await service.find("release", ctx, search_type="keywords")
    else:
        assert await service.find("release", ctx, search_type="keywords") is result
    find.assert_awaited_once_with(
        query="release",
        ctx=ctx,
        target_uri="",
        limit=10,
        score_threshold=None,
        filter=None,
        level=None,
        image_url=None,
        events_time_decay_protection=None,
        search_type="keywords",
    )
    get_store.assert_not_called()
    service.ensure_keywords_search_supported.assert_not_awaited()
