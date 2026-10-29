# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking_cli.retrieve import TypedQuery
from tests.unit.test_opengauss_public_retrieval import (
    _manager,
    ctx,  # noqa: F401
    isolated_config,  # noqa: F401
)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_mode", ["local", "vikingdb", "volcengine", "opengauss"])
@pytest.mark.parametrize("mode", [RetrieverMode.QUICK, RetrieverMode.THINKING])
@pytest.mark.parametrize("protection", ["0", "1d"])
async def test_keyword_decay_preserves_exact_baseline_call_and_result(ctx, backend_mode, mode, protection):  # noqa: F811
    calls, results = [], []
    for options in ({}, {"events_time_decay_protection": protection}):
        manager, backend = _manager(backend_mode)
        manager.get_account_backend.side_effect = [backend, AssertionError("extra backend lookup")]
        result = await HierarchicalRetriever(manager, None).retrieve(
            TypedQuery("release", None, ""), ctx, mode=mode, search_type="keywords", **options,
        )
        manager.get_account_backend.assert_awaited_once_with(ctx.account_id)
        manager._get_backend_for_context.assert_awaited_once_with(ctx)
        backend.search_by_keywords.assert_awaited_once()
        backend.query.assert_not_awaited()
        backend.get_collection_meta.assert_not_awaited()
        kwargs = backend.search_by_keywords.await_args.kwargs
        assert "events_time_decay_protection" not in kwargs
        assert "request_now" not in kwargs
        assert "advance" not in kwargs
        calls.append(backend.search_by_keywords.await_args)
        results.append([(hit.uri, hit.score) for hit in result.matched_contexts])
    assert calls[0] == calls[1]
    assert results[0] == results[1]
    assert len(results[0]) == 2
