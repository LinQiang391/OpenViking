from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import VikingFS


@pytest.fixture
def priority_fs():
    fs = VikingFS(agfs=SimpleNamespace())
    store = SimpleNamespace(
        _backend_type="opengauss",
        get_keyword_search_info=AsyncMock(return_value={"ready": True}),
        search_keywords_in_tenant=AsyncMock(return_value=[{"uri": "viking://resources/a"}]),
    )
    fs._get_vector_store = lambda: store
    fs._grep_in_files = AsyncMock(return_value={"count": 1, "matches": [{"uri": "viking://resources/a"}]})
    return fs, store


@pytest.mark.asyncio
async def test_priority_only_shortcuts_a_satisfied_limit(priority_fs):
    fs, _ = priority_fs
    result = await fs._grep_opengauss_priority("viking://resources", "word", None, False, 1, 10, None, 0)
    assert result["count"] == 1
    assert await fs._grep_opengauss_priority("viking://resources", "word", None, False, 2, 10, None, 0) is None


@pytest.mark.asyncio
async def test_empty_candidates_never_prove_no_match(priority_fs):
    fs, store = priority_fs
    store.search_keywords_in_tenant.return_value = []
    fs._grep_in_files.return_value = {"count": 0, "matches": []}
    assert await fs._grep_opengauss_priority("viking://resources", "word", None, False, 1, 10, None, 0) is None


@pytest.mark.asyncio
async def test_complex_regex_retains_full_fs_semantics(priority_fs):
    fs, store = priority_fs
    assert await fs._grep_opengauss_priority("viking://resources", r"foo.*bar", None, False, 1, 10, None, 0) is None
    store.search_keywords_in_tenant.assert_not_awaited()
