from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking_cli.retrieve import TypedQuery
from tests.unit.test_opengauss_public_retrieval import _manager, ctx, isolated_config  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('backend_mode',['vikingdb','volcengine'])
@pytest.mark.parametrize('mode',[RetrieverMode.QUICK,RetrieverMode.THINKING])
@pytest.mark.parametrize('protection',['0','1d'])
async def test_legacy_keyword_decay_remains_accepted(ctx,backend_mode,mode,protection):  # noqa: F811
    manager,backend=_manager(backend_mode)
    manager.search_by_keywords_in_tenant=AsyncMock(return_value=[])
    retriever=HierarchicalRetriever(manager,None)
    result=await retriever.retrieve(
        TypedQuery('release',None,''),ctx,mode=mode,search_type='keywords',
        events_time_decay_protection=protection,
    )
    assert result.matched_contexts==[]
    manager.search_by_keywords_in_tenant.assert_awaited_once()
    assert 'events_time_decay_protection' not in manager.search_by_keywords_in_tenant.await_args.kwargs
    backend.query.assert_not_awaited()
