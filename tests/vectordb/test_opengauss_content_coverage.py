"""Content CRUD contracts, without migration or coverage-state machinery."""

from copy import deepcopy

import pytest
from test_opengauss_acceptance_helper import acceptance_isolation  # noqa: F401
from test_opengauss_fresh_database import fresh, fresh_factory  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("method", ["upsert", "update_data"])
@pytest.mark.parametrize("text", ["", " ", "中文正文 retrieval"])
def test_content_mutation_preserves_input_and_roundtrips(fresh, method, text):  # noqa: F811
    fresh.upsert([{"id": "a", "content": "before"}])
    records = [{"id": "a", "content": text}]
    before = deepcopy(records)
    getattr(fresh, method)(records)
    assert records == before
    assert fresh.get(["a"])[0]["content"] == text
    getattr(fresh, method)([{"id": "a", "uri": "viking://resources/changed"}])
    row = fresh.get(["a"])[0]
    assert row["content"] == text
    assert "_ov_content_empty" not in row


@pytest.mark.parametrize("method", ["upsert", "update_data"])
@pytest.mark.parametrize("invalid", [0, False, [], {}, b"text", 1.25])
def test_invalid_content_type_rejects_entire_batch(fresh, method, invalid):  # noqa: F811
    fresh.upsert([{"id": "a", "content": "original"}, {"id": "b", "content": "original"}])
    before = fresh.get(["a", "b"])
    records = [{"id": "a", "content": "changed"}, {"id": "b", "content": invalid}]
    original = deepcopy(records)
    with pytest.raises((ValueError, TypeError), match="content|text"):
        getattr(fresh, method)(records)
    assert fresh.get(["a", "b"]) == before
    assert records == original


@pytest.mark.parametrize("method", ["upsert", "update_data"])
@pytest.mark.parametrize("flag", [True, False, None])
def test_internal_empty_marker_is_not_caller_controlled(fresh, method, flag):  # noqa: F811
    fresh.upsert([{"id": "a", "content": "original"}])
    with pytest.raises(ValueError, match="internal"):
        getattr(fresh, method)([{"id": "a", "content": None, "_ov_content_empty": flag}])
    assert fresh.get(["a"])[0]["content"] == "original"
