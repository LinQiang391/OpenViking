# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Boundary-complete BM25 pagination, plus opt-in dedicated-container checks."""

import random
import threading
import uuid
from unittest.mock import Mock
from urllib.parse import unquote

import pytest
from test_opengauss_acceptance_helper import acceptance_isolation, acceptance_target  # noqa: F401

from openviking.storage.vectordb_adapters.opengauss import fulltext
from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss.fulltext import OpenGaussFulltextMixin
from openviking_cli.exceptions import ResourceExhaustedError


class PageHarness(OpenGaussFulltextMixin):
    _normalize_filter_date_times = OpenGaussCollection._normalize_filter_date_times
    _row_to_dict = OpenGaussCollection._row_to_dict

    def __init__(self, conn=None, name="context"):
        self._conn = conn if conn is not None else Mock()
        self._cursor = self._conn.cursor
        self._lock = threading.RLock()
        self._name = name
        self._array_fields = set()
        self._field_names = {"id", "content", "tenant"}
        self._date_time_fields = set()
        self._meta = {
            "FullText": [{"Field": "content"}],
            "_fulltext_index": {"name": name + "_bm25", "engine": "bm25"},
        }
        self._get_all_columns = Mock(return_value=["id", "content", "tenant"])
        self._get_column_types = Mock(return_value={"content": "text"})
        self._execute = Mock(return_value=[(True, True, "bm25", "content", True, 1)])


def mock_page(rows):
    coll = PageHarness()
    cursor = coll._conn.cursor.return_value
    cursor.description = [("id",), ("_ov_fulltext_score",)]
    prefix = {"size": None}

    def execute(sql, params=None):
        if sql.startswith("SELECT"):
            assert "OFFSET" not in sql
            assert 'ORDER BY "content" <&> %s DESC LIMIT %s)' in sql
            assert 'ORDER BY _ov_fulltext_score DESC, "id"' in sql
            assert "tsquery" not in sql and "ts_rank" not in sql
            assert params[:-1] == ["retrieval", "retrieval"]
            prefix["size"] = params[-1]

    def fetchall():
        # Adversarial native tie order: prefix membership is unrelated to id.
        selected = rows[: prefix["size"]]
        return sorted((r for r in selected if r[-1] > 0), key=lambda r: (-r[-1], r[0]))

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = fetchall
    return coll, cursor


def candidate_limits(cursor):
    return [
        call.args[1][-1]
        for call in cursor.execute.call_args_list
        if call.args[0].startswith("SELECT")
    ]


def test_large_tie_group_is_complete_before_id_order_and_offset():
    rows = [(f"d{i:04}", 3.0) for i in range(1024, -1, -1)] + [("tail", 1.0)]
    coll, cursor = mock_page(rows)
    result = coll.search_by_keywords("default", query="retrieval", limit=7, offset=13)
    assert [hit.id for hit in result.data] == [f"d{i:04}" for i in range(13, 20)]
    assert candidate_limits(cursor) == [21, 42, 84, 168, 336, 672, 1344]
    assert cursor.execute.call_args_list[0].args == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
    )
    coll._conn.commit.assert_called_once()
    cursor.close.assert_called_once()


def test_pages_across_multiple_tie_groups_equal_one_global_order():
    rows = (
        [(f"a{i:03}", 9.0) for i in range(11, -1, -1)]
        + [(f"b{i:03}", 4.0) for i in range(74, -1, -1)]
        + [("tail", 1.0), ("zero", 0.0)]
    )
    coll, _ = mock_page(rows)
    actual = []
    for offset in range(0, 100, 9):
        actual.extend(
            hit.id
            for hit in coll.search_by_keywords(
                "default", query="retrieval", limit=9, offset=offset
            ).data
        )
    expected = [r[0] for r in sorted(rows, key=lambda r: (-r[-1], r[0])) if r[-1] > 0]
    assert actual == expected
    assert len(set(actual)) == len(actual)


@pytest.mark.parametrize("rows", [[], [("a", 1.0)], [("a", 1.0), ("z", 0.0)]])
def test_empty_short_and_beyond_end_pages(rows):
    coll, _ = mock_page(rows)
    assert coll.search_by_keywords("default", query="retrieval", limit=3, offset=4).data == []


def test_distinct_score_lookahead_proves_boundary_without_reading_corpus():
    coll, cursor = mock_page([(f"d{i}", 10.0 - i) for i in range(8)])
    result = coll.search_by_keywords("default", query="retrieval", limit=2, offset=1)
    assert [hit.id for hit in result.data] == ["d1", "d2"]
    assert candidate_limits(cursor) == [4]


def test_unresolved_tie_at_safety_cap_fails_without_partial_result(monkeypatch):
    monkeypatch.setattr(fulltext, "_BM25_MAX_CANDIDATES", 32)
    coll, cursor = mock_page([(f"d{i:03}", 1.0) for i in range(100, -1, -1)])
    with pytest.raises(ResourceExhaustedError, match="boundary tie group.*32.*safety limit"):
        coll.search_by_keywords("default", query="retrieval", limit=2)
    assert candidate_limits(cursor) == [3, 6, 12, 24, 32]
    coll._conn.commit.assert_not_called()
    coll._conn.rollback.assert_called_once()
    cursor.close.assert_called_once()


def test_native_hundred_thousand_tie_cap_is_not_relaxed():
    assert fulltext._BM25_MAX_CANDIDATES == 100_000
    coll, cursor = mock_page([(f"d{i:06}", 1.0) for i in range(100_000)])
    with pytest.raises(ResourceExhaustedError, match="boundary tie group") as error:
        coll.search_by_keywords("default", query="retrieval", limit=1)
    assert error.value.details == {"requested": 100_001, "max": 100_000}
    assert candidate_limits(cursor)[-1] == 100_000
    assert max(candidate_limits(cursor)) == 100_000
    coll._conn.commit.assert_not_called()
    coll._conn.rollback.assert_called_once()


def test_safety_cap_can_succeed_with_a_distinct_score_witness(monkeypatch):
    monkeypatch.setattr(fulltext, "_BM25_MAX_CANDIDATES", 8)
    coll, cursor = mock_page([(f"d{i}", 2.0) for i in range(6, -1, -1)] + [("tail", 1.0)])
    result = coll.search_by_keywords("default", query="retrieval", limit=7)
    assert [hit.id for hit in result.data] == [f"d{i}" for i in range(7)]
    assert candidate_limits(cursor) == [8]


def test_exactly_full_cap_without_lookahead_is_not_assumed_complete(monkeypatch):
    monkeypatch.setattr(fulltext, "_BM25_MAX_CANDIDATES", 8)
    coll, cursor = mock_page([(f"d{i}", 2.0) for i in range(7, -1, -1)])
    with pytest.raises(ResourceExhaustedError, match="boundary tie group.*8"):
        coll.search_by_keywords("default", query="retrieval", limit=2)
    assert candidate_limits(cursor) == [3, 6, 8]
    coll._conn.commit.assert_not_called()


def test_request_beyond_safety_cap_does_not_execute_candidates(monkeypatch):
    monkeypatch.setattr(fulltext, "_BM25_MAX_CANDIDATES", 8)
    coll, cursor = mock_page([])
    with pytest.raises(ResourceExhaustedError, match="8.*safety limit"):
        coll.search_by_keywords("default", query="retrieval", limit=2, offset=7)
    assert candidate_limits(cursor) == []
    coll._conn.rollback.assert_called_once()


@pytest.mark.parametrize("score", [float("inf"), float("nan")])
def test_nonfinite_candidates_are_rejected_even_outside_page(score):
    coll, cursor = mock_page([])
    cursor.fetchall.side_effect = None
    cursor.fetchall.return_value = [("a", 2.0), ("b", score)]
    with pytest.raises(ValueError, match="non-finite"):
        coll.search_by_keywords("default", query="retrieval", limit=1)
    coll._conn.rollback.assert_called_once()


def test_expansion_error_rolls_back_without_alternate_engine():
    coll, cursor = mock_page([])
    cursor.fetchall.side_effect = [[("z", 1.0), ("y", 1.0)], RuntimeError("timed out")]
    with pytest.raises(RuntimeError, match="timed out"):
        coll.search_by_keywords("default", query="retrieval", limit=1)
    assert candidate_limits(cursor) == [2, 4]
    assert all("ts_rank" not in call.args[0] for call in cursor.execute.call_args_list)
    coll._conn.commit.assert_not_called()
    coll._conn.rollback.assert_called_once()


@pytest.fixture
def dedicated_db(acceptance_isolation):  # noqa: F811
    psycopg2 = pytest.importorskip("psycopg2")
    target = acceptance_target()
    kwargs = {
        "host": target.hostname,
        "port": target.port,
        "dbname": "postgres",
        "user": unquote(target.username),
        "password": unquote(target.password or ""),
        "connect_timeout": 5,
        "options": f"-c search_path={acceptance_isolation},public -c statement_timeout=15000 -c client_encoding=UTF8",
    }
    conn = psycopg2.connect(**kwargs)
    name = "ov_page_" + uuid.uuid4().hex[:12]
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE "{name}" (id text PRIMARY KEY, content text, tenant text)'
            )
            rows = [(f"d{i:04}", "retrieval database", "a") for i in range(1025)]
            rows += [(f"p{i:04}", "retrieval database", "private") for i in range(73)]
            rows += [("low", "retrieval " + "noise " * 60, "a"), ("none", "unrelated", "a")]
            random.Random(7721).shuffle(rows)
            cursor.executemany(f'INSERT INTO "{name}" VALUES (%s,%s,%s)', rows)
            cursor.execute(f'CREATE INDEX "{name}_bm25" ON "{name}" USING bm25(content)')
        conn.commit()
        yield conn, name, kwargs
    finally:
        conn.rollback()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
            conn.commit()
        finally:
            conn.close()


@pytest.mark.integration
def test_database_native_bm25_requires_ordered_index_and_rejects_compound_sort(dedicated_db):
    conn, name, _ = dedicated_db
    hint = f"/*+ indexscan ({name} {name}_bm25) */"
    sql = (
        f'SELECT {hint} id, content <&> %s AS score FROM "{name}" '
        "ORDER BY content <&> %s DESC LIMIT 8"
    )
    with conn.cursor() as cursor:
        cursor.execute("EXPLAIN " + sql, ("retrieval", "retrieval"))
        plan = "\n".join(row[0] for row in cursor.fetchall())
        print("\nBM25 native plan:\n" + plan)
        assert "Index Scan" in plan and name + "_bm25" in plan
        assert "Order By:" in plan
        cursor.execute(sql, ("retrieval", "retrieval"))
        assert len(cursor.fetchall()) == 8
    conn.rollback()
    bounded = (
        f"SELECT * FROM (SELECT {hint} id, content <&> %s AS _ov_fulltext_score "
        f'FROM "{name}" WHERE tenant = %s ORDER BY content <&> %s DESC LIMIT %s) AS hits '
        "WHERE _ov_fulltext_score > 0 ORDER BY _ov_fulltext_score DESC, id"
    )
    with conn.cursor() as cursor:
        cursor.execute("EXPLAIN ANALYZE " + bounded, ("retrieval", "a", "retrieval", 2048))
        plan = "\n".join(row[0] for row in cursor.fetchall())
        print("\nBM25 boundary-complete prefix plan:\n" + plan)
        assert "Index Scan" in plan and name + "_bm25" in plan
        assert "Sort" in plan and "Limit" in plan
    conn.rollback()
    compound = sql.replace("DESC LIMIT", "DESC, id LIMIT")
    with conn.cursor() as cursor:
        cursor.execute("EXPLAIN " + compound, ("retrieval", "retrieval"))
        plan = "\n".join(row[0] for row in cursor.fetchall())
        print("\nBM25 compound plan (unsupported):\n" + plan)
        assert "Seq Scan" in plan and "Sort" in plan
        with pytest.raises(Exception, match="No BM25 index is used") as caught:
            cursor.execute(compound, ("retrieval", "retrieval"))
        assert caught.value.pgcode == "42809"
    conn.rollback()
    with conn.cursor() as cursor:
        with pytest.raises(Exception, match="No BM25 index is used"):
            cursor.execute(f'SELECT content <&> %s FROM "{name}" LIMIT 1', ("retrieval",))
    conn.rollback()


@pytest.mark.integration
def test_database_shuffled_large_tie_pages_filters_and_tail(dedicated_db):
    conn, name, _ = dedicated_db
    coll = PageHarness(conn, name)
    # Use real catalog validation and projection discovery, without unrelated
    # collection metadata tables or lifecycle DDL in the shared database.
    coll._execute = OpenGaussCollection._execute.__get__(coll)
    coll._get_all_columns = OpenGaussCollection._get_all_columns.__get__(coll)
    coll._get_column_types = OpenGaussCollection._get_column_types.__get__(coll)
    scope = {"op": "must", "field": "tenant", "conds": ["a"]}
    all_hits = coll.search_by_keywords(
        "default", query="retrieval", limit=2000, filters=scope, output_fields=["content", "tenant"]
    ).data
    assert [hit.id for hit in all_hits] == [f"d{i:04}" for i in range(1025)] + ["low"]
    assert all(hit.fields["tenant"] == "a" for hit in all_hits)
    paged = []
    for offset in range(0, 1200, 137):
        hits = coll.search_by_keywords(
            "default",
            query="retrieval",
            limit=137,
            offset=offset,
            filters=scope,
            output_fields=["tenant"],
        ).data
        paged.extend(hit.id for hit in hits)
    assert paged == [hit.id for hit in all_hits]
    assert len(set(paged)) == 1026
    assert coll.search_by_keywords("default", query="nevermatch", limit=5).data == []


@pytest.mark.integration
def test_database_safety_cap_raises_for_real_native_tie(dedicated_db, monkeypatch):
    conn, name, _ = dedicated_db
    monkeypatch.setattr(fulltext, "_BM25_MAX_CANDIDATES", 64)
    coll = PageHarness(conn, name)
    with pytest.raises(ResourceExhaustedError, match="boundary tie group.*64"):
        coll.search_by_keywords("default", query="retrieval", limit=3)
    assert conn.get_transaction_status() == 0
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
    conn.rollback()


@pytest.mark.integration
def test_database_expansions_share_repeatable_read_snapshot(dedicated_db):
    import psycopg2

    conn, name, kwargs = dedicated_db
    coll = PageHarness(conn, name)
    peer = psycopg2.connect(**kwargs)
    calls = []
    scores = []

    class InterleavedCursor:
        def __init__(self):
            self.cursor = conn.cursor()

        def __getattr__(self, attr):
            return getattr(self.cursor, attr)

        def execute(self, sql, params=None):
            if sql.startswith("SELECT"):
                calls.append(params[-1])
            return self.cursor.execute(sql, params)

        def fetchall(self):
            rows = self.cursor.fetchall()
            scores.append(rows[0][-1])
            if len(calls) == 1:
                with peer.cursor() as cursor:
                    cursor.execute(
                        f'INSERT INTO "{name}" VALUES (%s,%s,%s)',
                        ("a_concurrent", "retrieval database", "a"),
                    )
                peer.commit()
                with self.cursor.connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_isolation")
                    assert cursor.fetchone()[0] == "repeatable read"
                    cursor.execute(f'SELECT count(*) FROM "{name}" WHERE id=%s', ("a_concurrent",))
                    assert cursor.fetchone()[0] == 0
            return rows

    coll._cursor = InterleavedCursor
    try:
        result = coll.search_by_keywords("default", query="retrieval", limit=5)
        assert [hit.id for hit in result.data] == [f"d{i:04}" for i in range(5)]
        assert len(calls) > 1
        # Native BM25 corpus statistics can change despite MVCC repeatable
        # read. Each round must re-score the entire prefix, never append/merge
        # scores retained from an earlier round.
        print(f"\nConcurrent BM25 prefix sizes: {calls}; native scores: {scores}")
        assert (
            coll.search_by_keywords("default", query="retrieval", limit=1).data[0].id
            == "a_concurrent"
        )
    finally:
        peer.close()
