# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Offline sparse lifecycle, SQL shape, fusion and time-decay regression tests."""

import json
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss.retrieval import (
    MAX_SPARSE_NONZERO,
    OpenGaussRetrievalMixin,
    _candidate_limit,
    _parse_time_decay,
    _sparse_alpha,
    _time_decay_factor,
    _validate_sparse_vector,
)

if issubclass(OpenGaussCollection, OpenGaussRetrievalMixin):
    RetrievalCollection = OpenGaussCollection
else:

    class RetrievalCollection(OpenGaussRetrievalMixin, OpenGaussCollection):
        pass


@pytest.fixture
def coll():
    collection = RetrievalCollection.__new__(RetrievalCollection)
    collection._name = "context"
    collection._dim = 2
    collection._distance = "cosine"
    collection._distributed = False
    collection._lock = threading.RLock()
    collection._meta = {
        "_retrieval": {
            "sparse_enabled": True,
            "sparse_ready": True,
            "candidate_limit": 20,
            "sparse_weight": 0.25,
            "full_text_enabled": True,
            "full_text_fields": ["abstract"],
        }
    }
    collection._field_names = {"id", "vector", "sparse_vector", "updated_at", "tenant"}
    collection._array_fields = set()
    collection._date_time_fields = {"updated_at"}
    collection._indexes = {"default": {"_distance": "cosine"}}
    collection._pending_indexes = {}
    collection._conn = Mock()
    collection._cursor = Mock(return_value=collection._conn.cursor.return_value)
    collection._apply_search_params_on_cursor = Mock()
    cursor = collection._cursor.return_value
    cursor.description = [
        (name,)
        for name in ("id", "tenant", "_ov_dense_distance", "_ov_sparse_score", "_ov_decay_time")
    ]
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return collection


def compiled_advance(**updates):
    return {
        "time_decay": {
            "field": "updated_at",
            "origin_ms": 14 * 86400000,
            "offset_ms": 0,
            "scale_ms": 7 * 86400000,
            "decay": 0.5,
            **updates,
        }
    }


@pytest.mark.parametrize(
    "value",
    [
        {1: 1},
        {"": 1},
        {"bad\x00term": 1},
        {"a": -1},
        {"a": math.nan},
        {"a": math.inf},
        {"a": True},
        {"a": "1"},
        {"a": []},
        {"a" * 257: 1},
        {"界" * 86: 1},
        [],
        "[]",
        {"\ud800": 1},
    ],
)
def test_invalid_sparse_vectors_rejected(value):
    with pytest.raises(ValueError):
        _validate_sparse_vector(value)


def test_sparse_jsonb_validation_and_nonzero_limit():
    assert _validate_sparse_vector('{"x":2,"zero":0}') == {"x": 2.0}
    assert _validate_sparse_vector(None) == {}
    assert _validate_sparse_vector({"界" * 85: 1})
    assert len(_validate_sparse_vector({str(i): 1 for i in range(MAX_SPARSE_NONZERO)})) == 4096
    with pytest.raises(ValueError, match="nonzeros"):
        _validate_sparse_vector({str(i): 1 for i in range(MAX_SPARSE_NONZERO + 1)})
    assert _validate_sparse_vector({str(i): 0 for i in range(MAX_SPARSE_NONZERO + 1)}) == {}


@pytest.mark.parametrize("value", [0, -1, True, 2.5, 10001, "20"])
def test_candidate_limits_strictly_bounded(value):
    with pytest.raises(ValueError, match="candidate_limit"):
        _candidate_limit(value)


@pytest.mark.parametrize("value", [-0.01, 1.01, True, math.inf, "0.5"])
def test_alpha_is_finite_and_bounded(value):
    with pytest.raises(ValueError):
        _sparse_alpha(value)


def test_hybrid_sql_independent_recall_and_complete_scores(coll):
    cursor = coll._cursor.return_value
    cursor.fetchall.return_value = [
        ("dense-only-recall", "safe", 0.05, 0.8, None),
        ("sparse-only-recall", "safe", 0.4, 2.0, None),
    ]
    result = coll.search_ranked(
        "default",
        dense_vector=[1, 0],
        sparse_vector={"needle';--": 1.0},
        filters={"op": "must", "field": "tenant", "conds": ["safe';--"]},
        output_fields=["tenant"],
    )
    assert [item.id for item in result.data] == ["sparse-only-recall", "dense-only-recall"]
    assert [item.score for item in result.data] == pytest.approx([0.95, 0.9125])
    sql, params = cursor.execute.call_args.args
    assert "dense_candidates AS" in sql and "sparse_candidates AS" in sql
    assert " UNION " in sql
    assert 'ORDER BY "vector" <=> %s::vector LIMIT %s' in sql
    assert "JOIN query_sparse q ON p.term = q.term" in sql
    assert 'WHERE p.id = "context"."id"' in sql
    assert "_ov_dense_distance" in sql and "_ov_sparse_score" in sql
    assert sql.count("LIMIT %s") == 2
    assert "OFFSET" not in sql
    assert "needle';--" not in sql and "safe';--" not in sql
    assert params.count("safe';--") == 2
    assert params.count("[1.0,0.0]") == 2
    assert params[:2] == ["needle';--", 1.0]
    cursor.execute.assert_called_once()
    coll._conn.commit.assert_called_once()
    coll._conn.rollback.assert_not_called()
    coll._apply_search_params_on_cursor.assert_called_once_with(cursor, "default")


def test_sparse_only_needs_no_dense_index_or_dense_column(coll):
    coll._indexes = {}
    cursor = coll._cursor.return_value
    cursor.fetchall.return_value = [("s", "safe", None, 4.0, None)]
    result = coll.search_ranked("not_a_dense_index", sparse_vector={"term": 2})
    assert result.data[0].score == 4.0
    sql, params = cursor.execute.call_args.args
    assert "dense_candidates" not in sql
    assert "::vector" not in sql
    assert '"vector" IS NOT NULL' not in sql
    assert '"_ov_sparse_context"' in sql
    assert params == ["term", 2.0, 20]
    coll._apply_search_params_on_cursor.assert_not_called()


def test_dense_only_does_not_touch_postings(coll):
    cursor = coll._cursor.return_value
    cursor.fetchall.return_value = [("d", "safe", 0.2, 0, None)]
    result = coll.search_ranked("default", dense_vector=[1, 0])
    assert result.data[0].score == pytest.approx(0.8)
    sql = cursor.execute.call_args.args[0]
    assert "_ov_sparse_context" not in sql and "query_sparse" not in sql


@pytest.mark.parametrize(
    "metric,distance,expected",
    [
        ("cosine", 0.2, 0.8),
        ("ip", -2.0, 2.0),
        ("l2", 3, 0.25),
        ("l1", 1, 0.5),
    ],
)
def test_dense_metric_conversion(coll, metric, distance, expected):
    coll._indexes["default"]["_distance"] = metric
    coll._cursor.return_value.fetchall.return_value = [("d", "safe", distance, 0, None)]
    assert coll.search_ranked("default", dense_vector=[1, 0]).data[0].score == expected


def test_time_decay_rerank_before_final_pagination_and_no_field_leak(coll):
    origin = 14 * 86400000
    coll._cursor.return_value.fetchall.return_value = [
        ("old", "safe", 0.1, 0, origin - 7 * 86400000),
        ("recent", "safe", 0.2, 0, origin),
        ("middle", "safe", 0.3, 0, origin),
    ]
    result = coll.search_ranked(
        "default",
        dense_vector=[1, 0],
        limit=1,
        offset=1,
        output_fields=["tenant"],
        advance=compiled_advance(),
        return_detail_info=True,
    )
    assert [item.id for item in result.data] == ["middle"]
    item = result.data[0]
    assert item.score == pytest.approx(0.7)
    assert item.origin_score == pytest.approx(0.7)
    assert item.addition_score == 1.0
    assert item.fields == {"tenant": "safe"}
    sql, params = coll._cursor.return_value.execute.call_args.args
    assert '"updated_at" AS _ov_decay_time' in sql
    assert "OFFSET" not in sql and params[-2] == 20


def test_time_factor_matches_fixed_curve_and_missing_values():
    spec = _parse_time_decay(compiled_advance(offset_ms=86400000))
    assert _time_decay_factor(spec.origin_ms, spec) == 1
    assert _time_decay_factor(spec.origin_ms - 86400000, spec) == 1
    assert _time_decay_factor(spec.origin_ms - 8 * 86400000, spec) == pytest.approx(0.5)
    assert _time_decay_factor(spec.origin_ms + 8 * 86400000, spec) == pytest.approx(0.5)
    for value in [None, "", "invalid", math.inf, math.nan, True]:
        assert _time_decay_factor(value, spec) is None


def test_raw_time_decay_delegates_to_shared_helper(monkeypatch):
    module = ModuleType("openviking.utils.time_decay")
    spec = SimpleNamespace(field="updated_at")
    module.build_time_decay_fusion_spec = Mock(return_value=spec)
    monkeypatch.setitem(sys.modules, "openviking.utils.time_decay", module)
    assert (
        _parse_time_decay({"time_decay": {"protection": "2d", "origin": "2026-01-08T00:00:00Z"}})
        is spec
    )
    kwargs = module.build_time_decay_fusion_spec.call_args.kwargs
    assert kwargs["protection"] == "2d"
    assert kwargs["origin"].year == 2026 and kwargs["origin"].tzinfo is not None
    assert kwargs["field"] == "updated_at"


@pytest.mark.parametrize("advance", [{}, {"unknown": {}}, {"time_decay": {}, "unknown": 1}, []])
def test_unknown_advance_rejected_before_sql(coll, advance):
    with pytest.raises(NotImplementedError):
        coll.search_ranked("default", dense_vector=[1, 0], advance=advance)
    coll._cursor.assert_not_called()


@pytest.mark.parametrize(
    "updates",
    [
        {"offset_ms": -1},
        {"scale_ms": 0},
        {"decay": 1},
        {"decay": math.nan},
        {"origin_ms": True},
        {"field": "updated_at;DROP TABLE context"},
    ],
)
def test_invalid_compiled_decay_rejected_before_sql(coll, updates):
    with pytest.raises(ValueError):
        coll.search_ranked("default", dense_vector=[1, 0], advance=compiled_advance(**updates))
    coll._cursor.assert_not_called()


def test_unknown_time_field_and_oversized_paging_rejected(coll):
    with pytest.raises(ValueError, match="date_time"):
        coll.search_ranked("default", dense_vector=[1, 0], advance=compiled_advance(field="tenant"))
    with pytest.raises(ValueError, match="candidate_limit"):
        coll.search_ranked("default", sparse_vector={"x": 1}, limit=10, offset=11)
    with pytest.raises(ValueError, match="non-negative"):
        coll.search_ranked("default", limit=-1)
    coll._cursor.assert_not_called()


@pytest.mark.parametrize(
    "key,value,error",
    [
        ("sparse_enabled", False, ValueError),
        ("sparse_ready", False, RuntimeError),
    ],
)
def test_unready_sparse_query_never_creates_schema(coll, key, value, error):
    coll._meta["_retrieval"][key] = value
    with pytest.raises(error):
        coll.search_ranked("default", sparse_vector={"x": 1})
    coll._cursor.assert_not_called()


def test_query_does_not_materialize_pending_dense_index(coll):
    coll._indexes = {}
    coll._pending_indexes = {"default": {}}
    with pytest.raises(RuntimeError, match="build it explicitly"):
        coll.search_ranked("default", dense_vector=[1, 0])
    coll._cursor.assert_not_called()


def test_ranked_query_rollback_and_close_on_failure(coll):
    cursor = coll._cursor.return_value
    cursor.execute.side_effect = RuntimeError("query failed")
    with pytest.raises(RuntimeError, match="query failed"):
        coll.search_ranked("default", sparse_vector={"x": 1})
    coll._conn.rollback.assert_called_once()
    coll._conn.commit.assert_not_called()
    cursor.close.assert_called_once()


def test_sync_reads_actual_jsonb_and_preserves_partial_updates(coll):
    cursor = coll._cursor.return_value
    cursor.fetchone.side_effect = [(json.dumps(coll._meta),), None, None, None]
    coll._sync_sparse_postings_on_cursor(cursor, ["id';--", "id';--"])
    statements = [call.args for call in cursor.execute.call_args_list]
    assert len(statements) == 7
    assert "FOR UPDATE" in statements[0][0]
    assert all("id';--" not in sql for sql, *_ in statements)
    assert 'DELETE FROM "_ov_sparse_context" WHERE id IN (%s)' == statements[-2][0]
    assert statements[-2][1] == ["id';--"]
    assert "jsonb_each(c.sparse_vector)" in statements[-1][0]
    assert statements[-1][1] == ["id';--"]
    assert "CREATE" not in " ".join(sql for sql, *_ in statements)
    coll._conn.commit.assert_not_called()
    coll._conn.rollback.assert_not_called()
    cursor.close.assert_not_called()


def test_stale_handle_still_maintains_database_enabled_postings(coll):
    database_meta = json.dumps(coll._meta)
    coll._meta["_retrieval"]["sparse_enabled"] = False
    cursor = coll._cursor.return_value
    cursor.fetchone.side_effect = [(database_meta,), None, None, None]
    coll._sync_sparse_postings_on_cursor(cursor, ["updated"])
    assert any("INSERT INTO" in call.args[0] for call in cursor.execute.call_args_list)


def test_disabled_and_empty_mutation_hooks_no_ddl(coll):
    cursor = coll._cursor.return_value
    coll._sync_sparse_postings_on_cursor(cursor, [])
    coll._delete_sparse_postings_on_cursor(cursor, [])
    cursor.execute.assert_not_called()
    cursor.fetchone.return_value = (json.dumps({"_retrieval": {"sparse_enabled": False}}),)
    coll._sync_sparse_postings_on_cursor(cursor, ["id"])
    assert cursor.execute.call_count == 2
    assert "SELECT meta_json" in cursor.execute.call_args.args[0]


def test_delete_and_truncate_hooks_share_callers_transaction(coll):
    cursor = coll._cursor.return_value
    cursor.fetchone.return_value = (json.dumps(coll._meta),)
    coll._delete_sparse_postings_on_cursor(cursor, ["x';--"])
    sql, params = cursor.execute.call_args.args
    assert sql.startswith('DELETE FROM "_ov_sparse_context" WHERE id IN (%s)')
    assert 'NOT EXISTS (SELECT 1 FROM "context" c' in sql
    assert 'c."id" = "_ov_sparse_context".id' in sql
    assert params == ["x';--"]
    coll._delete_sparse_postings_on_cursor(cursor)
    assert cursor.execute.call_args.args == ('DELETE FROM "_ov_sparse_context"', [])
    coll._conn.commit.assert_not_called()


def test_invalid_stored_sparse_aborts_before_posting_deletion(coll):
    cursor = coll._cursor.return_value
    cursor.fetchone.side_effect = [(json.dumps(coll._meta),), (1,)]
    with pytest.raises(ValueError, match="JSON object"):
        coll._sync_sparse_postings_on_cursor(cursor, ["x"])
    assert not any("DELETE" in call.args[0] for call in cursor.execute.call_args_list)
    coll._conn.commit.assert_not_called()


def test_explicit_backfill_is_atomic_and_merges_shared_metadata(coll):
    cursor = coll._cursor.return_value
    database_meta = {**coll._meta, "Description": "another writer"}
    database_meta["_retrieval"] = {**coll._meta["_retrieval"], "full_text_ready": True}
    cursor.fetchone.side_effect = [None, None, None, (json.dumps(database_meta),)]
    coll.create_sparse_index(candidate_limit=30, sparse_weight=0.6)
    calls = [call.args for call in cursor.execute.call_args_list]
    assert calls[0][0] == 'LOCK TABLE "context" IN SHARE ROW EXCLUSIVE MODE'
    assert "PRIMARY KEY (id, term)" in calls[1][0]
    assert "(term, id)" in calls[2][0]
    assert 'DELETE FROM "_ov_sparse_context"' in [args[0] for args in calls]
    insert = next(args[0] for args in calls if args[0].startswith("INSERT INTO"))
    assert "jsonb_each(c.sparse_vector)" in insert
    assert not any("SELECT *" in args[0] for args in calls)
    assert "FOR UPDATE" in calls[-2][0]
    persisted = json.loads(calls[-1][1][0])
    assert persisted["Description"] == "another writer"
    assert persisted["_retrieval"]["full_text_ready"] is True
    assert persisted["_retrieval"]["full_text_fields"] == ["abstract"]
    assert persisted["_retrieval"]["candidate_limit"] == 30
    assert persisted["_retrieval"]["sparse_weight"] == 0.6
    assert persisted["_retrieval"]["sparse_ready"] is True
    assert coll._meta == persisted
    coll._conn.commit.assert_called_once()
    cursor.close.assert_called_once()


def test_backfill_validation_failure_rolls_back_ready_and_keeps_memory_metadata(coll):
    original = json.dumps(coll._meta)
    cursor = coll._cursor.return_value
    cursor.fetchone.side_effect = [None, (1,)]
    with pytest.raises(ValueError, match="negative weights"):
        coll.create_sparse_index()
    assert json.dumps(coll._meta) == original
    assert not any("UPDATE" in call.args[0] for call in cursor.execute.call_args_list)
    coll._conn.rollback.assert_called_once()
    coll._conn.commit.assert_not_called()
    cursor.close.assert_called_once()


def test_meta_commit_failure_does_not_publish_ready(coll):
    coll._meta["_retrieval"]["sparse_ready"] = False
    cursor = coll._cursor.return_value
    cursor.fetchone.side_effect = [None, None, None, (json.dumps(coll._meta),)]
    coll._conn.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError, match="commit failed"):
        coll.create_sparse_index()
    assert coll._meta["_retrieval"]["sparse_ready"] is False
    coll._conn.rollback.assert_called_once()


def test_derived_posting_identifiers_are_bounded_and_stable(coll):
    coll._name = "a" * 63
    name = coll._sparse_posting_table_name()
    assert len(name) <= 63
    assert name == coll._sparse_posting_table_name()
    coll._name = "a" * 62 + "b"
    assert name != coll._sparse_posting_table_name()


@pytest.mark.parametrize("alpha,expected", [(0.0, 0.6), (1.0, 3.0), (0.5, 1.8)])
def test_hybrid_alpha_endpoints_use_true_modality_scores(coll, alpha, expected):
    coll._meta["_retrieval"]["sparse_weight"] = alpha
    coll._cursor.return_value.fetchall.return_value = [("x", "safe", 0.4, 3.0, None)]
    result = coll.search_ranked("default", dense_vector=[1, 0], sparse_vector={"x": 1})
    assert result.data[0].score == pytest.approx(expected)


def test_missing_time_leaves_original_score_and_details_unset(coll):
    coll._cursor.return_value.fetchall.return_value = [("x", "safe", 0.2, 0, None)]
    item = coll.search_ranked(
        "default",
        dense_vector=[1, 0],
        advance=compiled_advance(),
        return_detail_info=True,
    ).data[0]
    assert item.score == item.origin_score == pytest.approx(0.8)
    assert item.addition_score is None


def test_nonfinite_completed_scores_are_not_silently_zeroed(coll):
    coll._cursor.return_value.fetchall.return_value = [("x", "safe", math.nan, 1, None)]
    with pytest.raises(ValueError, match="distance must be finite"):
        coll.search_ranked("default", dense_vector=[1, 0])
    coll._cursor.return_value.fetchall.return_value = [("x", "safe", None, math.inf, None)]
    with pytest.raises(ValueError, match="dot product must be finite"):
        coll.search_ranked("default", sparse_vector={"x": 1})


def test_backfill_requires_sparse_schema_before_any_sql(coll):
    coll._field_names.remove("sparse_vector")
    with pytest.raises(ValueError, match="JSONB sparse_vector"):
        coll.create_sparse_index()
    coll._cursor.assert_not_called()


@pytest.mark.parametrize(
    "updates",
    [
        {"scale_ms": 86400000},
        {"decay": 0.9},
        {"offset_ms": 1},
        {"offset_ms": 3000 * 365 * 86400000 + 60000},
        {"scale_ms": None},
        {"decay": False},
        {"field": None},
    ],
)
def test_public_compiled_payload_cannot_override_fixed_curve(coll, updates):
    with pytest.raises(ValueError):
        coll.search_ranked("default", dense_vector=[1, 0], advance=compiled_advance(**updates))
    coll._cursor.assert_not_called()


@pytest.mark.parametrize("protection", [None, False, 0, "", "-1d", "1.5d"])
def test_raw_protection_null_false_invalid(coll, protection):
    with pytest.raises(ValueError):
        coll.search_ranked(
            "default", dense_vector=[1, 0], advance={"time_decay": {"protection": protection}}
        )
    coll._cursor.assert_not_called()


def test_raw_decay_fixed_curve_null_origin_and_protection():
    spec = _parse_time_decay({"time_decay": {"protection": "2d", "origin": None}})
    assert spec.scale_ms == 7 * 86400000 and spec.decay == 0.5
    assert spec.offset_ms == 2 * 86400000 and math.isfinite(spec.origin_ms)
    with pytest.raises(ValueError):
        _parse_time_decay({"time_decay": {"protection": "0", "origin": False}})
    with pytest.raises(ValueError):
        _parse_time_decay({"time_decay": {"protection": "0", "field": None}})


@pytest.mark.parametrize(
    "key,value",
    [
        ("advance", False),
        ("advance", {"time_decay": None}),
        ("advance", {"time_decay": False}),
        ("dense_vector", False),
        ("sparse_vector", False),
        ("limit", None),
        ("offset", False),
        ("output_fields", False),
        ("filters", False),
    ],
)
def test_null_false_parameters_not_silently_ignored(coll, key, value):
    kwargs = {"dense_vector": [1, 0], key: value}
    with pytest.raises((ValueError, NotImplementedError)):
        coll.search_ranked("default", **kwargs)
    coll._cursor.assert_not_called()


@pytest.mark.parametrize("sparse", [None, {}, {"zero": 0}])
def test_empty_sparse_has_no_positive_overlap_candidates(coll, sparse):
    assert coll.search_ranked("default", sparse_vector=sparse).data == []
    coll._cursor.assert_not_called()
    coll._cursor.return_value.fetchall.return_value = [("dense", "safe", 1.4, 0, None)]
    result = coll.search_ranked("default", dense_vector=[1, 0], sparse_vector=sparse)
    assert result.data[0].score == pytest.approx(-0.4)
    assert "sparse_candidates" not in coll._cursor.return_value.execute.call_args.args[0]


def test_nonoverlap_sparse_returns_empty_not_zero_padding(coll):
    assert coll.search_ranked("default", sparse_vector={"absent": 1}).data == []
    sql = coll._cursor.return_value.execute.call_args.args[0]
    assert "JOIN query_sparse q ON p.term = q.term" in sql
    assert "HAVING SUM(p.weight * q.weight) > 0" in sql


def test_hybrid_negative_dense_and_zero_nonoverlap_scores(coll):
    coll._cursor.return_value.fetchall.return_value = [
        ("overlap", "safe", 2.0, 0.1, None),
        ("nonoverlap-dense", "safe", 1.1, 0.0, None),
    ]
    result = coll.search_ranked("default", dense_vector=[1, 0], sparse_vector={"x": 1})
    assert [item.id for item in result.data] == ["nonoverlap-dense", "overlap"]
    assert [item.score for item in result.data] == pytest.approx([-0.075, -0.725])


@pytest.mark.parametrize("hybrid", [False, True])
def test_per_modality_and_union_candidate_bounds(coll, hybrid):
    budget = coll._meta["_retrieval"]["candidate_limit"]
    count = budget * (2 if hybrid else 1)
    coll._cursor.return_value.fetchall.return_value = [
        (str(i), "safe", 0.2 if hybrid else None, float(i + 1), None) for i in range(count)
    ]
    result = coll.search_ranked(
        "default",
        dense_vector=[1, 0] if hybrid else None,
        sparse_vector={"x": 1},
        limit=budget,
    )
    assert len(result.data) == budget
    sql, params = coll._cursor.return_value.execute.call_args.args
    assert sql.count("LIMIT %s") == (2 if hybrid else 1)
    assert params.count(budget) == (2 if hybrid else 1)
    assert (" UNION " in sql) is hybrid
    with pytest.raises(ValueError, match="candidate_limit"):
        coll.search_ranked("default", sparse_vector={"x": 1}, limit=budget + 1)


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
@pytest.mark.parametrize(
    "filter_value,fragment,values",
    [
        (
            {"op": "must", "field": "tenant", "conds": ["safe';--"]},
            '"tenant" IN (%s)',
            ["safe';--"],
        ),
        (
            {"op": "must_not", "field": "tenant", "conds": ["blocked"]},
            '"tenant" NOT IN (%s)',
            ["blocked"],
        ),
        ({"op": "prefix", "field": "tenant", "prefix": "safe_"}, '"tenant" LIKE %s', ["safe\\_%"]),
        ({"op": "contains", "field": "tenant", "substring": "x%"}, '"tenant" LIKE %s', ["%x\\%%"]),
        (
            {"op": "range", "field": "updated_at", "gte": 10, "lt": 20},
            '"updated_at" >= %s AND "updated_at" < %s',
            [10, 20],
        ),
        (
            {
                "op": "and",
                "conds": [
                    {"op": "must", "field": "tenant", "conds": ["safe"]},
                    {"op": "or", "conds": []},
                ],
            },
            '("tenant" IN (%s)) AND (FALSE)',
            ["safe"],
        ),
        ({"op": "must", "field": "tenant", "conds": []}, "FALSE", []),
    ],
)
def test_filters_bound_and_applied_to_each_recall_branch(
    coll, mode, filter_value, fragment, values
):
    coll.search_ranked(
        "default",
        dense_vector=[1, 0] if mode != "sparse" else None,
        sparse_vector={"x": 1} if mode != "dense" else None,
        filters=filter_value,
    )
    sql, params = coll._cursor.return_value.execute.call_args.args
    branches = 2 if mode == "hybrid" else 1
    assert sql.count(fragment) == branches
    for value in values:
        extra = branches if value == coll._meta["_retrieval"]["candidate_limit"] else 0
        assert params.count(value) == branches + extra
    assert "safe';--" not in sql
    assert sql.count("%s") == len(params)


@pytest.mark.parametrize(
    "filters",
    [
        False,
        [],
        {"op": "and", "conds": None},
        {"op": "or", "conds": [False]},
        {"op": "and", "conds": [{"op": "must", "field": "missing", "conds": [1]}]},
        {"op": "must", "field": "tenant", "conds": None},
        {"op": "must", "field": "tenant", "conds": "not-a-list"},
    ],
)
def test_invalid_filter_branches_rejected_even_on_empty_query(coll, filters):
    with pytest.raises(ValueError):
        coll.search_ranked("default", filters=filters, limit=0)
    coll._cursor.assert_not_called()


def test_projection_and_rank_alias_collisions_are_preserved(coll):
    coll._field_names.update({"_ov_candidate_id", "_ov_dense_distance"})
    cursor = coll._cursor.return_value
    cursor.description = [
        (name,)
        for name in (
            "id",
            "_ov_candidate_id",
            "_ov_dense_distance",
            "_ov_dense_distance",
            "_ov_sparse_score",
            "_ov_decay_time",
        )
    ]
    cursor.fetchall.return_value = [("x", "user-id", "user-distance", 0.25, 0, None)]
    result = coll.search_ranked(
        "default",
        dense_vector=[1, 0],
        output_fields=["_ov_candidate_id", "_ov_dense_distance"],
    )
    assert result.data[0].fields == {
        "_ov_candidate_id": "user-id",
        "_ov_dense_distance": "user-distance",
    }
    sql = cursor.execute.call_args.args[0]
    assert (
        'SELECT "context"."id", "context"."_ov_candidate_id", "context"."_ov_dense_distance"' in sql
    )
    assert 'WHERE "context"."id" IN (SELECT candidates._ov_candidate_id FROM candidates)' in sql
    assert "JOIN candidates ON" not in sql


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
def test_candidate_recall_and_completion_share_single_statement_snapshot(coll, mode):
    cursor = coll._cursor.return_value
    order = []
    coll._apply_search_params_on_cursor.side_effect = lambda *args: order.append("set-local")
    cursor.execute.side_effect = lambda *args: order.append("select")
    cursor.fetchall.side_effect = lambda: order.append("fetch") or []
    coll._conn.commit.side_effect = lambda: order.append("commit")
    cursor.close.side_effect = lambda: order.append("close")
    coll.search_ranked(
        "default",
        dense_vector=[1, 0] if mode != "sparse" else None,
        sparse_vector={"x": 1} if mode != "dense" else None,
    )
    cursor.execute.assert_called_once()
    sql = cursor.execute.call_args.args[0]
    assert sql.startswith("WITH ") and ";" not in sql
    assert "candidates AS" in sql and "_ov_dense_distance" in sql
    assert order == (["set-local"] if mode != "sparse" else []) + [
        "select",
        "fetch",
        "commit",
        "close",
    ]


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
def test_ranked_search_threads_do_not_wait_for_collection_rlock(coll, mode):
    barrier = threading.Barrier(2, timeout=3)
    local = threading.local()
    cursors = []
    cursors_lock = threading.Lock()

    def make_cursor():
        cursor = Mock()
        cursor.description = [
            (name,) for name in ("id", "_ov_dense_distance", "_ov_sparse_score", "_ov_decay_time")
        ]
        cursor.execute.side_effect = lambda *args: barrier.wait()
        cursor.fetchall.return_value = [
            (threading.current_thread().name, 0.2 if mode != "sparse" else None, 1.0, None)
        ]
        local.cursor = cursor
        with cursors_lock:
            cursors.append(cursor)
        return cursor

    def commit():
        assert local.cursor.execute.call_count == 1
        assert local.cursor.close.call_count == 0

    coll._cursor = Mock(side_effect=make_cursor)
    coll._conn.commit.side_effect = commit
    # Holding the actual RLock in a third thread (this test thread) makes any
    # accidental search lock acquisition fail deterministically, not by timing.
    with ThreadPoolExecutor(max_workers=2) as executor:
        with coll._lock:
            futures = [
                executor.submit(
                    coll.search_ranked,
                    "default",
                    dense_vector=[1, 0] if mode != "sparse" else None,
                    sparse_vector={"x": 1} if mode != "dense" else None,
                )
                for _ in range(2)
            ]
            try:
                results = [future.result(timeout=5) for future in futures]
            finally:
                barrier.abort()
    assert len({result.data[0].id for result in results}) == 2
    assert len(cursors) == 2 and cursors[0] is not cursors[1]
    for cursor in cursors:
        cursor.execute.assert_called_once()
        cursor.fetchall.assert_called_once()
        cursor.close.assert_called_once()
    assert coll._conn.commit.call_count == 2
    coll._conn.rollback.assert_not_called()


def test_backfill_still_holds_collection_lock(coll):
    lock = Mock()
    entered = []
    lock.__enter__ = Mock(side_effect=lambda: entered.append(True))
    lock.__exit__ = Mock(side_effect=lambda *args: entered.pop())
    coll._lock = lock
    cursor = coll._cursor.return_value
    cursor.fetchone.side_effect = [None, None, None, (json.dumps(coll._meta),)]

    def execute(*args):
        assert entered == [True]

    cursor.execute.side_effect = execute
    coll.create_sparse_index()
    lock.__enter__.assert_called_once()
    lock.__exit__.assert_called_once()
    assert entered == []


@pytest.mark.parametrize("field,array", [("tenant", False), ("tags", True)])
@pytest.mark.parametrize("op", ["must", "must_not"])
def test_array_and_scalar_filter_branches(coll, field, array, op):
    coll._field_names.add(field)
    if array:
        coll._array_fields.add(field)
    coll.search_ranked(
        "default",
        dense_vector=[1, 0],
        sparse_vector={"x": 1},
        filters={"op": op, "field": field, "conds": ["a", "b"]},
    )
    sql, params = coll._cursor.return_value.execute.call_args.args
    if array:
        assert sql.count(f'ANY("{field}")') == 4
    else:
        assert sql.count(f'"{field}" {"NOT IN" if op == "must_not" else "IN"} (%s, %s)') == 2
    assert params.count("a") == params.count("b") == 2


def test_nested_date_path_and_or_filters_applied_to_both_branches(coll):
    coll._field_names.add("uri")
    coll.search_ranked(
        "default",
        dense_vector=[1, 0],
        sparse_vector={"x": 1},
        filters={
            "op": "or",
            "conds": [
                {"op": "must", "field": "uri", "conds": ["/safe"], "para": "-d=1"},
                {"op": "range", "field": "updated_at", "gte": "2026-01-01T00:00:00Z"},
            ],
        },
    )
    sql, params = coll._cursor.return_value.execute.call_args.args
    assert sql.count(" OR ") >= 2
    assert params.count("/safe") == 2
    assert params.count(1767225600000) == 2
    assert sql.count("%s") == len(params)


def test_projection_null_legacy_content_is_not_table_qualified(coll):
    coll.search_ranked("default", dense_vector=[1, 0], output_fields=["content"])
    sql = coll._cursor.return_value.execute.call_args.args[0]
    assert 'SELECT "context"."id", NULL AS "content",' in sql
    assert '"context".NULL' not in sql


def test_spq_search_and_ddl_rejected_before_cursor(coll):
    coll._distributed = True
    with pytest.raises(NotImplementedError, match="SPQ"):
        coll.search_ranked("default", sparse_vector={"x": 1})
    with pytest.raises(NotImplementedError, match="SPQ"):
        coll.create_sparse_index()
    coll._cursor.assert_not_called()


def test_drop_is_explicit_and_uses_callers_cursor(coll):
    cursor = coll._cursor.return_value
    coll._drop_sparse_postings_on_cursor(cursor)
    cursor.execute.assert_called_once_with('DROP TABLE IF EXISTS "_ov_sparse_context"')
    coll._conn.commit.assert_not_called()
