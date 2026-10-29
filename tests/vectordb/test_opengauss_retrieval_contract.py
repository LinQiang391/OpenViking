import threading
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss.retrieval import _time_decay_factor
from openviking.utils.time_decay import build_time_decay_fusion_spec
from openviking_cli.exceptions import UnimplementedError
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig, VectorDBBackendConfig


def test_sparse_weight_needs_no_connection_level_opt_in():
    config = VectorDBBackendConfig(
        backend="opengauss", sparse_weight=0.5, opengauss=OpenGaussConfig()
    )
    assert config.sparse_weight == 0.5


def test_content_usage_is_a_stable_adapter_contract():
    from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter

    assert OpenGaussCollectionAdapter.USE_CONTENT_FIELD is True


def test_wrapper_forwards_new_options_only_when_requested():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection.search_by_vector = Mock(return_value=SearchResult())
    collection.search_by_keywords = Mock(return_value=SearchResult())
    collection.close = Mock()
    wrapper = Collection(collection)
    advance = {"time_decay": {"protection": "0"}}
    wrapper.search_by_vector("default", [1.0], advance=advance, return_detail_info=True)
    assert collection.search_by_vector.call_args.kwargs == {
        "advance": advance,
        "return_detail_info": True,
    }
    wrapper.search_by_keywords("default", query="hello", mode="bm25", fields=["content"])
    assert collection.search_by_keywords.call_args.kwargs["mode"] == "bm25"
    assert collection.search_by_keywords.call_args.kwargs["fields"] == ["content"]
    assert collection.search_by_keywords.call_args.kwargs["query"] == "hello"


def test_random_unsupported_ranking_is_explicit():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    with pytest.raises(UnimplementedError, match="advanced"):
        collection.search_by_random("default", advance={"unknown": True})


@pytest.mark.parametrize("dense,sparse", [(None, None), ([], {})])
def test_adapter_zero_limit_empty_query_with_decay_skips_random_dimension(dense, sparse):
    from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter

    adapter = OpenGaussCollectionAdapter.__new__(OpenGaussCollectionAdapter)
    adapter._distributed = False
    adapter._index_name = "default"
    collection = Mock()
    adapter.get_collection = Mock(return_value=collection)
    assert adapter.query(
        query_vector=dense, sparse_query_vector=sparse, limit=0,
        advance={"time_decay": {"protection": "0"}}, output_fields=[],
    ) == []
    assert collection.search_by_vector.call_args.kwargs["limit"] == 0
    assert collection.search_by_vector.call_args.kwargs["dense_vector"] == dense
    collection.get_meta_data.assert_not_called()


def test_schema_type_change_cannot_succeed_as_metadata_only():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._meta = {"Fields": [{"FieldName": "name", "FieldType": "string"}]}
    collection._lock = threading.RLock()
    collection._name = "context"
    collection._conn = Mock()
    collection._cursor = collection._conn.cursor
    collection._load_metadata_on_cursor = Mock(return_value=collection._meta)
    with pytest.raises(ValueError, match="explicit migration"):
        collection.update(fields=[{"FieldName": "name", "FieldType": "int64"}])
    assert collection._meta["Fields"][0]["FieldType"] == "string"


def test_fixed_time_decay_contract():
    origin = datetime(2026, 1, 15, tzinfo=timezone.utc)
    spec = build_time_decay_fusion_spec(protection="7d", origin=origin)
    assert _time_decay_factor(datetime(2026, 1, 8, tzinfo=timezone.utc), spec) == 1
    assert _time_decay_factor(datetime(2026, 1, 1, tzinfo=timezone.utc), spec) == pytest.approx(0.5)
    assert _time_decay_factor(datetime(2026, 1, 29, tzinfo=timezone.utc), spec) == pytest.approx(0.5)
    assert _time_decay_factor(None, spec) is None


def test_score_details_are_optional():
    item = SearchItemResult(id="x", score=0.4, origin_score=0.8, addition_score=0.5)
    assert item.score == item.origin_score * item.addition_score
