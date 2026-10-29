"""Standard schema/index acceptance, isolated to the dedicated disposable DB."""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import unquote

import pytest
from test_opengauss_acceptance_helper import acceptance_isolation, acceptance_target  # noqa: F401

from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def feature_adapter(acceptance_isolation):  # noqa: F811
    parsed = acceptance_target()
    name = f"ov_features_{uuid.uuid4().hex[:12]}"
    adapter = OpenGaussCollectionAdapter(name, OpenGaussConfig(
        host=parsed.hostname, port=parsed.port, user=unquote(parsed.username),
        password=unquote(parsed.password or ""), db_name="postgres",
    ))
    try:
        adapter.create_collection(name, {
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "account_id", "FieldType": "string"},
                {"FieldName": "updated_at", "FieldType": "date_time"},
                {"FieldName": "content", "FieldType": "text"},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 8},
            ],
            "ScalarIndex": ["account_id"],
            "FullText": [{"Field": "content"}],
        }, distance="cosine", sparse_weight=0.5, index_name="default")
        adapter.upsert([
            {"id": "old", "uri": "viking://resources/old", "account_id": "a",
             "updated_at": "2026-01-01T00:00:00Z", "sparse_vector": {"rare": 1.0},
             "content": "retrieval database old", "vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
            {"id": "new", "uri": "viking://resources/new", "account_id": "a",
             "updated_at": "2026-01-15T00:00:00Z", "sparse_vector": {"rare": 2.0},
             "content": "retrieval database new", "vector": [0.0, 1, 0, 0, 0, 0, 0, 0]},
            {"id": "private", "uri": "viking://resources/private", "account_id": "b",
             "updated_at": "2026-01-15T00:00:00Z", "sparse_vector": {"rare": 100.0},
             "content": "retrieval database private", "vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
        ])
        yield adapter
    finally:
        adapter.close()


def _feature_sql(adapter, statement, params=None):
    cursor = adapter._conn.cursor()
    try:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(statement, params)
        rows = cursor.fetchall() if cursor.description else []
        adapter._conn.commit()
        return rows
    except Exception:
        adapter._conn.rollback()
        raise
    finally:
        cursor.close()


def _feature_metadata(adapter):
    rows = _feature_sql(adapter, 'SELECT meta_json FROM "_ov_collection_meta" WHERE table_name = %s',
                        (adapter.collection_name,))
    assert len(rows) == 1
    return json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]


def _feature_rows(adapter):
    return _feature_sql(adapter, f'SELECT id, uri, account_id, updated_at, sparse_vector '
                        f'FROM "{adapter.collection_name}" ORDER BY id')


def _feature_postings(adapter):
    name = adapter.get_collection()._Collection__collection._sparse_posting_table_name()
    return _feature_sql(adapter, f'SELECT id, term, weight FROM "{name}" ORDER BY id, term')


@pytest.fixture
def feature_peer(feature_adapter):
    adapter = feature_adapter
    peer = OpenGaussCollectionAdapter(adapter.collection_name, OpenGaussConfig(
        host=adapter._host, port=adapter._port, user=adapter._user,
        password=adapter._password, db_name=adapter._db_name,
    ))
    try:
        peer.get_collection()
        assert peer._pool is not adapter._pool
        yield peer
    finally:
        peer.close()


def test_sparse_fusion_and_transactional_mutation(feature_adapter):
    adapter = feature_adapter
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    hits = adapter.query(sparse_query_vector={"rare": 1.0}, filter=scope)
    assert [hit["id"] for hit in hits] == ["new", "old"]
    hits = adapter.query(query_vector=[1.0, 0, 0, 0, 0, 0, 0, 0],
                         sparse_query_vector={"rare": 1.0}, filter=scope)
    assert {hit["id"] for hit in hits} == {"old", "new"}
    adapter.update_data([{"id": "new", "sparse_vector": {}}])
    assert [hit["id"] for hit in adapter.query(sparse_query_vector={"rare": 1.0}, filter=scope)] == ["old"]
    adapter.delete(ids=["old"])
    assert adapter.query(sparse_query_vector={"rare": 1.0}, filter=scope) == []


def test_fulltext_filter_and_collection_drop(feature_adapter):
    adapter = feature_adapter
    collection = adapter.get_collection()._Collection__collection
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    hits = adapter.search_by_keywords(query="retrieval", mode="bm25", filter=scope, fields=["content"])
    assert {hit["id"] for hit in hits} == {"new", "old"}
    assert collection.validate_fulltext_index("bm25")
    assert adapter.drop_collection()
    assert not adapter.collection_exists()


def test_database_fulltext_index_path_is_available(feature_adapter):
    from test_opengauss_fresh_database import physical_indexes

    adapter = feature_adapter
    index = next(row[0] for row in physical_indexes(adapter) if row[1] == "bm25")
    statement = (f'SELECT /*+ indexscan ({adapter.collection_name} {index}) */ id '
                 f'FROM "{adapter.collection_name}" ORDER BY content <&> %s DESC LIMIT 2')
    plan = "\n".join(row[0] for row in _feature_sql(adapter, "EXPLAIN ANALYZE " + statement, ("retrieval",)))
    assert index in plan and "Index Scan" in plan, plan


def test_decay_is_applied_before_pagination(feature_adapter):
    hits = feature_adapter.query(
        query_vector=[1.0, 1, 0, 0, 0, 0, 0, 0],
        filter={"op": "must", "field": "account_id", "conds": ["a"]},
        advance={"time_decay": {"protection": "0", "origin": "2026-01-15T00:00:00Z"}}, limit=1,
    )
    assert hits[0]["id"] == "new"


def test_peer_description_preserves_retrieval_metadata(feature_adapter, feature_peer):
    before = deepcopy(_feature_metadata(feature_adapter))
    feature_peer.get_collection().update(description="peer description")
    after = _feature_metadata(feature_adapter)
    assert after["Description"] == "peer description"
    assert after.get("_sparse_index") == before.get("_sparse_index")
    assert _feature_postings(feature_adapter) == [
        ("new", "rare", 2.0), ("old", "rare", 1.0), ("private", "rare", 100.0),
    ]


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
def test_database_sparse_partial_update_preserves_postings(feature_adapter, feature_peer, method):
    before = _feature_postings(feature_adapter)
    getattr(feature_peer.get_collection(), method)([{"id": "old", "account_id": "changed"}, {"id": "new"}])
    assert _feature_postings(feature_adapter) == before
    rows = {row[0]: row for row in _feature_rows(feature_adapter)}
    assert rows["old"][2] == "changed"
    assert rows["old"][4] == {"rare": 1.0} and rows["new"][4] == {"rare": 2.0}
    hits = feature_adapter.query(sparse_query_vector={"rare": 1.0},
                                 filter={"op": "must", "field": "account_id", "conds": ["changed"]})
    assert [(hit["id"], hit["_score"]) for hit in hits] == [("old", 1.0)]


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
@pytest.mark.parametrize("invalid_sparse", [{"rare": -1.0}, {"rare": "not-a-number"}])
def test_database_invalid_sparse_rolls_back_base_and_postings(feature_adapter, feature_peer, method, invalid_sparse):
    before_rows = _feature_rows(feature_adapter)
    before_postings = _feature_postings(feature_adapter)
    with pytest.raises(ValueError, match="sparse.*invalid|negative.*weights|sparse weight.*finite"):
        getattr(feature_peer.get_collection(), method)([
            {"id": "old", "account_id": "must-roll-back", "sparse_vector": {"fresh": 3.0}},
            {"id": "new", "account_id": "must-roll-back", "sparse_vector": invalid_sparse},
        ])
    assert _feature_rows(feature_adapter) == before_rows
    assert _feature_postings(feature_adapter) == before_postings
    assert _feature_sql(feature_peer, "SELECT 1") == [(1,)]


@pytest.mark.parametrize("limit", [None, 1])
def test_database_filtered_delete_and_truncate_keep_postings_consistent(feature_adapter, feature_peer, limit):
    assert feature_peer.delete(filter={"op": "must", "field": "account_id", "conds": ["a"]},
                               limit=limit) == (2 if limit is None else 1)
    expected_ids = {"private"} if limit is None else {"old", "private"}
    assert {row[0] for row in _feature_rows(feature_adapter)} == expected_ids
    assert {row[0] for row in _feature_postings(feature_adapter)} == expected_ids
    assert {hit["id"] for hit in feature_adapter.query(sparse_query_vector={"rare": 1.0})} == expected_ids
    feature_peer.get_collection().delete_all_data()
    assert _feature_rows(feature_adapter) == [] and _feature_postings(feature_adapter) == []
    assert feature_adapter.query(sparse_query_vector={"rare": 1.0}) == []


def test_database_pool_thread_local_transactions_are_isolated(feature_adapter):
    adapter = feature_adapter
    workers = min(4, adapter._connection_pool_max_size)
    assert workers >= 2
    barrier = threading.Barrier(workers)

    def query_transaction(worker):
        cursor = adapter._conn.cursor()
        row_id = ("new", "old", "private")[worker % 3]
        try:
            cursor.execute("SET LOCAL statement_timeout = 15000")
            cursor.execute("SELECT pg_catalog.set_config('application_name', %s, true), pg_backend_pid()",
                           (f"ov_feature_worker_{worker}",))
            _, backend = cursor.fetchone()
            barrier.wait(timeout=10)
            cursor.execute(f"SELECT current_setting('application_name'), pg_backend_pid(), id, account_id "
                           f'FROM "{adapter.collection_name}" WHERE id = %s', (row_id,))
            assert cursor.fetchall() == [(f"ov_feature_worker_{worker}", backend, row_id,
                                           "b" if row_id == "private" else "a")]
            barrier.wait(timeout=10)
            if worker % 2:
                adapter._conn.rollback()
            else:
                adapter._conn.commit()
            cursor.execute("SELECT current_setting('application_name'), pg_backend_pid()")
            reset_name, reset_backend = cursor.fetchone()
            assert not reset_name.startswith("ov_feature_worker_")
            assert reset_backend == backend
            adapter._conn.commit()
            return backend
        except Exception:
            barrier.abort()
            adapter._conn.rollback()
            raise
        finally:
            cursor.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(query_transaction, worker) for worker in range(workers)]
        backends = [future.result(timeout=30) for future in futures]
    assert len(set(backends)) == workers
    assert _feature_sql(adapter, "SELECT 1") == [(1,)]


def test_database_pool_multithreaded_ranked_queries(feature_adapter):
    adapter = feature_adapter
    workers = adapter._connection_pool_max_size + 4
    barrier = threading.Barrier(workers)
    queries = [
        {"sparse_query_vector": {"rare": 1.0}},
        {"query_vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
        {"query_vector": [1.0, 0, 0, 0, 0, 0, 0, 0], "sparse_query_vector": {"rare": 1.0}},
    ]
    expected = {}
    for account in ("a", "b"):
        for kind, query in enumerate(queries):
            hits = adapter.query(**query, filter={"op": "must", "field": "account_id", "conds": [account]})
            assert {hit["id"] for hit in hits} == ({"old", "new"} if account == "a" else {"private"})
            expected[account, kind] = [(hit["id"], hit["_score"]) for hit in hits]

    def query_repeatedly(worker):
        samples = []
        barrier.wait(timeout=10)
        for iteration in range(8):
            account = "a" if (worker + iteration) % 2 else "b"
            kind = (worker + iteration) % len(queries)
            started = time.perf_counter()
            hits = adapter.query(**queries[kind], filter={"op": "must", "field": "account_id", "conds": [account]})
            samples.append((time.perf_counter() - started) * 1000)
            assert [hit["id"] for hit in hits] == [key for key, _ in expected[account, kind]]
            assert [hit["_score"] for hit in hits] == pytest.approx([score for _, score in expected[account, kind]])
            assert all(hit["account_id"] == account for hit in hits)
        return samples

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(query_repeatedly, worker) for worker in range(workers)]
        samples = [sample for future in futures for sample in future.result(timeout=45)]
    assert len(samples) == workers * 8
    assert _feature_sql(adapter, "SELECT 1") == [(1,)]


class _FeatureCursorProbe:
    """Observe/trigger at execute boundaries without replacing driver results."""

    def __init__(self, cursor, execute):
        self._cursor = cursor
        self._execute = execute

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, statement, params=None):
        return self._execute(self._cursor, statement, params)


def _feature_ranked_query(kind):
    query = {}
    if kind in {"dense", "hybrid"}:
        query["query_vector"] = [1.0, 1, 0, 0, 0, 0, 0, 0]
    if kind in {"sparse", "hybrid"}:
        query["sparse_query_vector"] = {"rare": 1.0}
    return query


def _feature_decay():
    return {"time_decay": {"protection": "0", "origin": "2026-01-15T00:00:00Z"}}


@pytest.mark.parametrize("kind", ["dense", "sparse", "hybrid"])
@pytest.mark.parametrize("limit,offset", [(1002, 0), (2, 1000)])
def test_database_boundary_default_budget_expands_real_sql(
    feature_adapter, monkeypatch, kind, limit, offset,
):
    """Three rows prove SQL/budget plumbing, not 1002-row recall or ANN scale."""
    adapter = feature_adapter
    collection = adapter.get_collection()._Collection__collection
    assert adapter._retrieval_config["candidate_limit"] == 1000
    assert collection._meta.get("_sparse_index", {}).get("candidate_limit", 1000) == 1000
    completed = []
    original_cursor = collection._cursor

    def execute(cursor, statement, params):
        result = cursor.execute(statement, params)
        if statement.startswith("WITH ") and "candidates AS" in statement:
            completed.append((statement, tuple(params)))
        return result

    monkeypatch.setattr(collection, "_cursor", lambda: _FeatureCursorProbe(original_cursor(), execute))
    hits = adapter.query(
        **_feature_ranked_query(kind), advance=_feature_decay(), limit=limit, offset=offset,
    )
    assert {hit["id"] for hit in hits} == ({"old", "new", "private"} if offset == 0 else set())
    assert len(completed) == 1
    statement, params = completed[0]
    assert ("dense_candidates AS" in statement) == (kind != "sparse")
    assert ("sparse_candidates AS" in statement) == (kind != "dense")
    assert params.count(1002) == (2 if kind == "hybrid" else 1)
    assert 1000 not in params
    assert _feature_sql(adapter, "SELECT 1") == [(1,)]


@pytest.mark.parametrize("kind", ["dense", "sparse", "hybrid"])
@pytest.mark.parametrize("limit,offset", [(10001, 0), (2, 9999)])
def test_database_boundary_budget_over_max_has_details(feature_adapter, kind, limit, offset):
    from openviking_cli.exceptions import ResourceExhaustedError

    with pytest.raises(ResourceExhaustedError) as raised:
        feature_adapter.query(
            **_feature_ranked_query(kind), advance=_feature_decay(), limit=limit, offset=offset,
        )
    assert raised.value.code == "RESOURCE_EXHAUSTED"
    assert raised.value.details == {"requested": 10001, "max": 10000}
    assert "requested=10001" in str(raised.value) and "max=10000" in str(raised.value)
    assert _feature_sql(feature_adapter, "SELECT 1") == [(1,)]


@pytest.mark.parametrize("dense,sparse", [(None, None), ([], {})])
def test_database_boundary_zero_limit_empty_decay(feature_adapter, dense, sparse):
    assert feature_adapter.query(
        query_vector=dense, sparse_query_vector=sparse, limit=0,
        advance=_feature_decay(), output_fields=[],
    ) == []
    assert _feature_sql(feature_adapter, "SELECT 1") == [(1,)]


@pytest.mark.parametrize("limit", [0, 1])
@pytest.mark.parametrize("invalid,match", [
    ({"limit": True}, "limit and offset"),
    ({"limit": -1}, "limit and offset"),
    ({"limit": 1.5}, "limit and offset"),
    ({"limit": "1"}, "limit and offset"),
    ({"offset": True}, "limit and offset"),
    ({"offset": -1}, "limit and offset"),
    ({"offset": 0.5}, "limit and offset"),
    ({"offset": "0"}, "limit and offset"),
    ({"output_fields": "id"}, "output_fields must be a list"),
    ({"output_fields": ("id",)}, "output_fields must be a list"),
    ({"output_fields": [True]}, "output_fields must be a list"),
    ({"output_fields": ["id", None]}, "output_fields must be a list"),
    ({"output_fields": ["not_declared"]}, "Unknown openGauss output fields"),
    ({"filter": {"op": "and", "conds": [
        {"op": "must", "field": "account_id", "conds": ["a"]},
        {"op": "or", "conds": [
            {"op": "must", "field": "not_declared", "conds": ["x"]},
        ]},
    ]}}, "Unknown openGauss filter field"),
    ({"filter": {"op": "and", "conds": True}}, "conds must be a list"),
    ({"filter": {"op": "or", "conds": [True]}}, "branches must be non-empty objects"),
    ({"filter": {"op": "must", "field": "account_id", "conds": True}}, "conds must be a list"),
    ({"advance": {"time_decay": {"protection": "0", "unknown": True}}}, "optional origin/field only"),
    ({"advance": {"time_decay": {"protection": "0", "field": "not_declared"}}}, "declared date_time field"),
])
def test_database_boundary_invalid_query(feature_adapter, limit, invalid, match):
    """Live collection validation; invalid requests need not reach database SQL."""
    request = {**_feature_ranked_query("dense"), "advance": _feature_decay(), "limit": limit}
    request.update(invalid)
    with pytest.raises(ValueError, match=match):
        feature_adapter.query(**request)
    assert _feature_sql(feature_adapter, "SELECT 1") == [(1,)]


def test_database_boundary_owned_backend_disconnect_preserves_driver_error(
    feature_adapter, acceptance_isolation, monkeypatch,  # noqa: F811
):
    """Injected backend termination, not a spontaneous DB/ANN system failure."""
    import psycopg2

    adapter = feature_adapter
    collection = adapter.get_collection()._Collection__collection
    before_rows = _feature_rows(adapter)
    before_postings = _feature_postings(adapter)
    target = acceptance_target()
    controller = psycopg2.connect(
        host=target.hostname, port=target.port, user=unquote(target.username),
        password=unquote(target.password or ""), dbname="postgres", connect_timeout=10,
        options=f"-c search_path={acceptance_isolation},public -c statement_timeout=15000",
    )
    controller.autocommit = True
    original_cursor = collection._cursor
    captured = []
    owned = []
    owned_connections = []
    statements_after_disconnect = []
    target_insert = f'INSERT INTO "{adapter.collection_name}" '

    def execute(cursor, statement, params):
        if captured:
            statements_after_disconnect.append(statement)
        if statement.startswith(target_insert) and not owned:
            # The earlier UPDATE is uncommitted on this exact cursor/connection.
            cursor.execute(
                f'SELECT pg_backend_pid(), current_schema(), account_id '
                f'FROM "{adapter.collection_name}" WHERE id = %s', ("old",),
            )
            backend, schema, uncommitted_account = cursor.fetchone()
            assert schema == acceptance_isolation
            assert uncommitted_account == "disconnect-must-roll-back"
            with controller.cursor() as control:
                control.execute("SELECT pg_backend_pid(), current_schema()")
                control_backend, control_schema = control.fetchone()
                assert control_backend != backend and control_schema == schema
                owned.append(backend)
                owned_connections.append(cursor.connection)
                control.execute("SELECT pg_terminate_backend(%s)", (backend,))
                assert control.fetchone() == (True,)
            try:
                return cursor.execute(statement, params)
            except psycopg2.Error as error:
                captured.append((error, error.pgcode, error.diag.sqlstate, cursor.connection.closed))
                raise
        return cursor.execute(statement, params)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(collection, "_cursor", lambda: _FeatureCursorProbe(original_cursor(), execute))
            with pytest.raises(psycopg2.Error) as raised:
                adapter.upsert([
                    {"id": "old", "account_id": "disconnect-must-roll-back"},
                    {"id": "disconnect_target", "account_id": "a", "sparse_vector": {"rare": 3.0}},
                ])
        assert len(owned) == 1 and len(captured) == 1
        original, pgcode, sqlstate, closed = captured[0]
        assert raised.value is original
        assert raised.value.pgcode == pgcode and raised.value.diag.sqlstate == sqlstate
        assert isinstance(original, psycopg2.OperationalError)
        assert not isinstance(original, psycopg2.InterfaceError)
        assert closed
        assert not statements_after_disconnect, statements_after_disconnect
        assert _feature_rows(adapter) == before_rows
        assert _feature_postings(adapter) == before_postings
        # openGauss may reuse a worker/thread PID; connection identity proves replacement.
        recovered_cursor = adapter._conn.cursor()
        try:
            assert recovered_cursor.connection is not owned_connections[0]
            assert owned_connections[0].closed and not recovered_cursor.connection.closed
            recovered_cursor.execute("SELECT pg_backend_pid(), current_schema()")
            recovered_backend, recovered_schema = recovered_cursor.fetchone()
            assert recovered_schema == acceptance_isolation
            adapter._conn.commit()
        finally:
            recovered_cursor.close()
        adapter.upsert([{"id": "recovered", "account_id": "a", "sparse_vector": {"rare": 4.0}}])
        assert ("recovered", "rare", 4.0) in _feature_postings(adapter)
        assert _feature_sql(adapter, "SELECT 1") == [(1,)]
        print(
            f"\nOPENGAUSS_INJECTED_OWNED_DISCONNECT schema={acceptance_isolation} "
            f"owned_pid={owned[0]} exception={type(original).__name__} pgcode={pgcode!r} "
            f"identity_preserved=true pool_recovered=true recovered_pid={recovered_backend}",
            flush=True,
        )
    finally:
        controller.close()
