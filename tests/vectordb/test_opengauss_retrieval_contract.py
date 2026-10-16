import threading
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.utils.time_decay import build_time_decay_fusion_spec, time_decay_factor
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig, VectorDBBackendConfig


def test_sparse_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="sparse_enabled"):
        VectorDBBackendConfig(backend="opengauss", sparse_weight=0.5, opengauss=OpenGaussConfig())
    config = VectorDBBackendConfig(backend="opengauss", sparse_weight=0.5, opengauss=OpenGaussConfig(sparse_enabled=True))
    assert config.sparse_weight == 0.5


def test_new_retrieval_not_advertised_for_spq():
    with pytest.raises(ValueError, match="SPQ"):
        OpenGaussConfig(mode="distributed", sparse_enabled=True)


def test_wrapper_forwards_new_options_only_when_requested():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection.search_by_vector = Mock(return_value=SearchResult())
    collection.search_by_keywords = Mock(return_value=SearchResult())
    collection.close = Mock()
    wrapper = Collection(collection)
    advance = {"time_decay": {"protection": "0"}}
    wrapper.search_by_vector("default", [1.0], advance=advance, return_detail_info=True)
    assert collection.search_by_vector.call_args.kwargs == {"advance": advance, "return_detail_info": True}
    wrapper.search_by_keywords("default", query="hello", mode="bm25", fields=["content"])
    assert collection.search_by_keywords.call_args.kwargs == {"mode": "bm25", "fields": ["content"]}


def test_random_unsupported_ranking_is_explicit():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    with pytest.raises(NotImplementedError, match="advanced"):
        collection.search_by_random("default", advance={"unknown": True})


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
    assert time_decay_factor(datetime(2026, 1, 8, tzinfo=timezone.utc), spec) == 1
    assert time_decay_factor(datetime(2026, 1, 1, tzinfo=timezone.utc), spec) == pytest.approx(0.5)
    assert time_decay_factor(datetime(2026, 1, 29, tzinfo=timezone.utc), spec) == pytest.approx(0.5)
    assert time_decay_factor(None, spec) == 1


def test_score_details_are_optional():
    item = SearchItemResult(id="x", score=0.4, origin_score=0.8, addition_score=0.5)
    assert item.score == item.origin_score * item.addition_score
