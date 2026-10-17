"""Compatibility checks replace external I/O, not the public backend methods."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, RawDSL
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier
from tests.storage.test_opengauss_retrieval_upper import make_backend


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['vikingdb', 'volcengine'])
@pytest.mark.parametrize('fields', [None, ['content'], ['title', 'body']])
@pytest.mark.parametrize('search_mode', [None, 'bm25', 'custom'])
async def test_legacy_keyword_payload_and_metadata_independence(mode, fields, search_mode):
    backend, _, adapter = make_backend({}, mode=mode)
    backend.get_keyword_search_info = AsyncMock(side_effect=RuntimeError('metadata unavailable'))
    original = {'op':'must','field':'status','conds':['ready']}
    result = await backend.search_by_keywords(
        keywords=['one'], query='query', mode=search_mode, fields=fields,
        limit=7, offset=3, filter=original, output_fields=['id', 'uri'],
    )
    assert result == [{'id':'one','_score':1.5}]
    adapter.search_by_keywords.assert_called_once_with(
        keywords=['one'], query='query', mode=search_mode, fields=fields,
        limit=7, offset=3, filter=And([Eq('account_id','acct'),RawDSL(original)]),
        output_fields=['id','uri'],
    )
    backend.get_keyword_search_info.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['vikingdb', 'volcengine', 'opengauss'])
async def test_legacy_vs_opengauss_decay_partition(mode):
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade.acl_manager = None
    facade._get_backend_for_context = AsyncMock(return_value=SimpleNamespace(_mode=mode))
    facade.search = AsyncMock(return_value=[])
    ctx = RequestContext(user=UserIdentifier('acct','alice'),role=Role.USER)
    scope=Eq('status','ready')
    await facade._search_with_event_time_decay(
        ctx=ctx,query_vector=[1.],sparse_query_vector=None,scope_filter=scope,
        limit=3,offset=1,events_time_decay_protection='1d',request_now=datetime(2026,1,1,tzinfo=timezone.utc),
    )
    regular,event=facade.search.call_args_list
    if mode != 'opengauss':
        assert event.kwargs['filter'] == And([scope,Eq('search_tags','memory_type=events')])
        assert regular.kwargs['filter'] == And([scope,RawDSL({'op':'must_not','field':'search_tags','conds':['memory_type=events']})])
    else:
        assert event.kwargs['filter'] == And([scope,And([Eq('context_type','memory'),Eq('search_tags','memory_type=events')])])
    assert 'advance' not in regular.kwargs
    assert event.kwargs['advance']['time_decay']['protection']=='1d'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,atomic,expected', [('vikingdb',True,False),('volcengine',True,False),('opengauss',False,False),('opengauss',True,True)])
async def test_content_backfill_capability_is_independent_of_text_storage(mode,atomic,expected):
    backend,_,adapter=make_backend({'content':True},mode=mode)
    if atomic:
        adapter.backfill_content_record=Mock()
    facade=VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade._get_backend_for_context=AsyncMock(return_value=backend)
    ctx=RequestContext(user=UserIdentifier('acct','alice'),role=Role.USER)
    assert await facade.supports_atomic_content_backfill(ctx=ctx) is expected
    policy=await facade.get_content_policy(ctx=ctx)
    assert policy['source']==('full_source' if mode=='opengauss' else 'legacy')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['vikingdb', 'volcengine'])
@pytest.mark.parametrize('enabled', [False, True])
async def test_legacy_content_switch_does_not_borrow_or_probe(mode, enabled):
    backend, _, adapter = make_backend({}, mode=mode)
    adapter.USE_CONTENT_FIELD = enabled
    backend.get_content_policy = AsyncMock(side_effect=AssertionError('dynamic policy forbidden'))
    backend._retired = True
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade.get_account_backend = AsyncMock(return_value=backend)

    assert await facade.account_uses_content_field('acct') is enabled
    facade.get_account_backend.assert_awaited_once_with('acct')
    backend.get_content_policy.assert_not_awaited()


@pytest.mark.asyncio
async def test_opengauss_content_switch_retains_dynamic_readiness():
    backend, _, _ = make_backend({}, mode='opengauss')
    backend.get_content_policy = AsyncMock(side_effect=[{'enabled': False}, {'enabled': True}])
    facade = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    facade.get_account_backend = AsyncMock(return_value=backend)

    assert await facade.account_uses_content_field('acct') is False
    assert await facade.account_uses_content_field('acct') is True
    assert backend.get_content_policy.await_count == 2
