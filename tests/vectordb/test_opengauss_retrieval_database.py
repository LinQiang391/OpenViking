"""Opt-in feature acceptance: DSN must point at a disposable standalone test DB."""

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import unquote, urlparse

import pytest

from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def feature_adapter():
    dsn = os.getenv("OPENVIKING_OPENGAUSS_FEATURE_DSN")
    if not dsn:
        pytest.skip("set OPENVIKING_OPENGAUSS_FEATURE_DSN to a disposable standalone database")
    parsed = urlparse(dsn)
    name = f"ov_features_{uuid.uuid4().hex[:12]}"
    adapter = OpenGaussCollectionAdapter(
        name,
        OpenGaussConfig(
            host=parsed.hostname or "127.0.0.1", port=parsed.port or 5432,
            user=unquote(parsed.username or "gaussdb"), password=unquote(parsed.password or ""),
            db_name=parsed.path.lstrip("/") or "omm", sparse_enabled=True,
        ),
    )
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
        }, distance="cosine", sparse_weight=0.5, index_name="default")
        adapter.upsert([
            {"id": "old", "uri": "viking://resources/old", "account_id": "a",
             "updated_at": "2026-01-01T00:00:00Z", "sparse_vector": {"rare": 1.0},
             "vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
            {"id": "new", "uri": "viking://resources/new", "account_id": "a",
             "updated_at": "2026-01-15T00:00:00Z", "sparse_vector": {"rare": 2.0},
             "vector": [0.0, 1, 0, 0, 0, 0, 0, 0]},
            {"id": "private", "uri": "viking://resources/private", "account_id": "b",
             "updated_at": "2026-01-15T00:00:00Z", "sparse_vector": {"rare": 100.0},
             "vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
        ])
        yield adapter
    finally:
        adapter.drop_collection()
        adapter.close()


def test_sparse_fusion_and_transactional_mutation(feature_adapter):
    adapter = feature_adapter
    collection = adapter.retrieval_collection()
    collection.create_sparse_index(sparse_weight=0.5)
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    hits = adapter.query(sparse_query_vector={"rare": 1.0}, filter=scope)
    assert [hit["id"] for hit in hits] == ["new", "old"]
    hits = adapter.query(query_vector=[1.0, 0, 0, 0, 0, 0, 0, 0], sparse_query_vector={"rare": 1.0}, filter=scope)
    assert {hit["id"] for hit in hits} == {"old", "new"}
    adapter.update_data([{"id": "new", "sparse_vector": {}}])
    assert [hit["id"] for hit in adapter.query(sparse_query_vector={"rare": 1.0}, filter=scope)] == ["old"]
    adapter.delete(ids=["old"])
    assert adapter.query(sparse_query_vector={"rare": 1.0}, filter=scope) == []


@pytest.mark.parametrize("engine", ["fts", "bm25"])
def test_fulltext_backfill_filter_and_rebuild(feature_adapter, engine):
    adapter = feature_adapter
    capabilities = adapter.probe_capabilities()
    if capabilities[engine]["state"] != "available":
        pytest.skip(f"test instance does not advertise {engine}")
    collection = adapter.retrieval_collection()
    collection.configure_fulltext(engine)
    collection.migrate_content_schema(allow_ddl=True)
    for _ in range(10):
        result = collection.backfill_content_batch(lambda row: "retrieval database " + row["id"], batch_size=1)
        if result["done"]:
            break
    assert result["done"]
    collection.build_fulltext_index(allow_ddl=True)
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    hits = adapter.search_by_keywords(query="retrieval", mode=engine, filter=scope, fields=["content"])
    assert {hit["id"] for hit in hits} == {"new", "old"}
    raw = adapter._conn.cursor()
    try:
        statement = (f'EXPLAIN SELECT id FROM "{collection._name}" WHERE account_id=%s '
                     + ('ORDER BY content <&> %s DESC LIMIT 2' if engine == 'bm25'
                        else "AND pg_catalog.to_tsvector('simple'::regconfig, coalesce(content, ''::text)) "
                             "@@ pg_catalog.plainto_tsquery('simple'::regconfig, %s)"))
        raw.execute("SET LOCAL enable_seqscan=off")
        raw.execute(statement, ("a", "retrieval"))
        plan = "\n".join(row[0] for row in raw.fetchall())
        assert collection._meta["_retrieval"]["index"]["name"] in plan
        adapter._conn.commit()
    finally:
        raw.close()
    collection.rebuild_fulltext_index(allow_ddl=True)
    assert collection.validate_fulltext_index(engine)
    collection.drop_fulltext_index(allow_ddl=True)
    with pytest.raises(RuntimeError, match="ready"):
        adapter.search_by_keywords(query="retrieval", mode=engine)


def test_decay_is_applied_before_pagination(feature_adapter):
    adapter = feature_adapter
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    hits = adapter.query(query_vector=[1.0, 1, 0, 0, 0, 0, 0, 0], filter=scope,
                         advance={"time_decay": {"protection": "0", "origin": "2026-01-15T00:00:00Z"}}, limit=1)
    assert hits[0]["id"] == "new"


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
    rows = _feature_sql(
        adapter,
        'SELECT meta_json FROM "_ov_collection_meta" WHERE table_name = %s',
        (adapter.collection_name,),
    )
    assert len(rows) == 1
    return json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]


def _feature_rows(adapter):
    return _feature_sql(
        adapter,
        f'SELECT id, uri, account_id, updated_at, sparse_vector '
        f'FROM "{adapter.collection_name}" ORDER BY id',
    )


def _feature_postings(adapter):
    name = adapter.retrieval_collection()._sparse_posting_table_name()
    return _feature_sql(adapter, f'SELECT id, term, weight FROM "{name}" ORDER BY id, term')


@pytest.fixture
def feature_peer(feature_adapter):
    """A separately pooled handle loaded before the test's retrieval migrations."""
    adapter = feature_adapter
    peer = OpenGaussCollectionAdapter(
        adapter.collection_name,
        OpenGaussConfig(
            host=adapter._host,
            port=adapter._port,
            user=adapter._user,
            password=adapter._password,
            db_name=adapter._db_name,
            sparse_enabled=False,
        ),
    )
    try:
        peer.retrieval_collection()
        assert peer._pool is not adapter._pool
        yield peer
    finally:
        peer.close()


@pytest.mark.parametrize("changed_field", ["updated_at", "content"])
def test_database_backfill_snapshot_conflict_is_atomic(
    feature_adapter, feature_peer, changed_field
):
    adapter = feature_adapter
    collection = adapter.retrieval_collection()
    collection.migrate_content_schema(allow_ddl=True)
    first = collection.backfill_content_batch(lambda row: "first " + row["id"], batch_size=1)
    assert first["checkpoint"]["after_id"] == "new"
    before_meta = deepcopy(_feature_metadata(adapter)["_retrieval"])
    before_rows = _feature_sql(
        adapter, f'SELECT id, updated_at, content FROM "{adapter.collection_name}" ORDER BY id'
    )
    snapshot_ready = threading.Event()
    writer_committed = threading.Event()
    snapshots = []
    replacement = 1792108800000 if changed_field == "updated_at" else "concurrent authoritative text"

    def load(snapshot):
        snapshots.append(snapshot)
        # The final row conflicts after an earlier row has already been loaded.
        if snapshot["id"] == "private":
            snapshot_ready.set()
            assert writer_committed.wait(timeout=10), "concurrent writer did not commit"
        return "backfilled " + snapshot["id"]

    def write_after_snapshot():
        assert snapshot_ready.wait(timeout=10), "backfill did not reach loader barrier"
        try:
            _feature_sql(
                feature_peer,
                f'UPDATE "{adapter.collection_name}" SET "{changed_field}" = %s WHERE id = %s',
                (replacement, "private"),
            )
        finally:
            writer_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        backfill = pool.submit(collection.backfill_content_batch, load, batch_size=2)
        writer = pool.submit(write_after_snapshot)
        writer.result(timeout=20)
        with pytest.raises(RuntimeError, match="snapshot changed"):
            backfill.result(timeout=20)

    assert [row["id"] for row in snapshots] == ["old", "private"]
    expected = [list(row) for row in before_rows]
    expected[-1][1 if changed_field == "updated_at" else 2] = replacement
    actual = _feature_sql(
        feature_peer,
        f'SELECT id, updated_at, content FROM "{adapter.collection_name}" ORDER BY id',
    )
    assert actual == [tuple(row) for row in expected]
    assert _feature_metadata(feature_peer)["_retrieval"] == before_meta
    assert collection._meta["_retrieval"] == before_meta


def test_database_stale_description_preserves_retrieval_metadata(feature_adapter, feature_peer):
    collection = feature_adapter.retrieval_collection()
    stale = feature_peer.retrieval_collection()
    before = deepcopy(stale._meta.get("_retrieval", {}))
    collection.migrate_content_schema(allow_ddl=True)
    collection.create_sparse_index(sparse_weight=0.25, candidate_limit=64)
    collection.backfill_content_batch(lambda row: "persisted " + row["id"], batch_size=1)
    retrieval = deepcopy(_feature_metadata(feature_adapter)["_retrieval"])
    assert retrieval != before
    assert stale._meta.get("_retrieval", {}) == before
    stale.update(description="description from pre-migration handle")
    persisted = _feature_metadata(feature_adapter)
    assert persisted["Description"] == "description from pre-migration handle"
    assert persisted["_retrieval"] == retrieval
    assert stale._meta["_retrieval"] == retrieval
    assert _feature_postings(feature_adapter) == [
        ("new", "rare", 2.0), ("old", "rare", 1.0), ("private", "rare", 100.0)
    ]


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
def test_database_pre_migration_handle_writes_migrated_content(
    feature_adapter, feature_peer, method
):
    stale = feature_peer.retrieval_collection()
    assert "content" not in stale._get_all_columns()
    assert stale._meta.get("_retrieval", {}).get("schema_ready") is not True
    feature_adapter.retrieval_collection().migrate_content_schema(allow_ddl=True)
    metadata = deepcopy(_feature_metadata(feature_adapter))
    assert stale._meta.get("_retrieval", {}).get("schema_ready") is not True
    getattr(stale, method)([{"id": "old", "content": "text written through old handle"}])
    assert _feature_sql(
        feature_adapter,
        f'SELECT content FROM "{feature_adapter.collection_name}" WHERE id = %s',
        ("old",),
    ) == [("text written through old handle",)]
    assert _feature_metadata(feature_adapter) == metadata


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
def test_database_sparse_partial_update_preserves_postings(feature_adapter, feature_peer, method):
    adapter = feature_adapter
    stale = feature_peer.retrieval_collection()
    assert stale._meta.get("_retrieval", {}).get("sparse_enabled") is not True
    adapter.retrieval_collection().create_sparse_index()
    before = _feature_postings(adapter)
    getattr(stale, method)([{"id": "old", "account_id": "changed"}, {"id": "new"}])
    assert _feature_postings(adapter) == before
    rows = {row[0]: row for row in _feature_rows(adapter)}
    assert rows["old"][2] == "changed"
    assert rows["old"][4] == {"rare": 1.0}
    assert rows["new"][4] == {"rare": 2.0}
    hits = adapter.query(
        sparse_query_vector={"rare": 1.0},
        filter={"op": "must", "field": "account_id", "conds": ["changed"]},
    )
    assert [(hit["id"], hit["_score"]) for hit in hits] == [("old", 1.0)]


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
@pytest.mark.parametrize("invalid_sparse", [{"rare": -1.0}, {"rare": "not-a-number"}])
def test_database_invalid_sparse_rolls_back_base_and_postings(
    feature_adapter, feature_peer, method, invalid_sparse
):
    adapter = feature_adapter
    stale = feature_peer.retrieval_collection()
    adapter.retrieval_collection().create_sparse_index()
    # A stale handle bypasses Python's enabled-sparse precheck, exercising DB rollback.
    assert stale._meta.get("_retrieval", {}).get("sparse_enabled") is not True
    before_rows = _feature_rows(adapter)
    before_postings = _feature_postings(adapter)
    before_metadata = deepcopy(_feature_metadata(adapter))
    with pytest.raises(ValueError, match="sparse.*invalid|negative weights"):
        getattr(stale, method)([
            {"id": "old", "account_id": "must-roll-back", "sparse_vector": {"fresh": 3.0}},
            {"id": "new", "account_id": "must-roll-back", "sparse_vector": invalid_sparse},
        ])
    assert _feature_rows(adapter) == before_rows
    assert _feature_postings(adapter) == before_postings
    assert _feature_metadata(adapter) == before_metadata
    assert _feature_sql(feature_peer, "SELECT 1") == [(1,)]


@pytest.mark.parametrize("limit", [None, 1])
def test_database_filtered_delete_and_truncate_keep_postings_consistent(
    feature_adapter, feature_peer, limit
):
    adapter = feature_adapter
    adapter.retrieval_collection().create_sparse_index()
    assert feature_peer._retrieval_config["sparse_enabled"] is False
    assert feature_peer.delete(
        filter={"op": "must", "field": "account_id", "conds": ["a"]}, limit=limit
    ) == (2 if limit is None else 1)
    expected_ids = {"private"} if limit is None else {"old", "private"}
    assert {row[0] for row in _feature_rows(adapter)} == expected_ids
    expected_postings = [("private", "rare", 100.0)]
    if limit is not None:
        expected_postings.insert(0, ("old", "rare", 1.0))
    assert _feature_postings(adapter) == expected_postings
    assert {hit["id"] for hit in adapter.query(sparse_query_vector={"rare": 1.0})} == expected_ids
    feature_peer.get_collection().delete_all_data()
    assert _feature_rows(adapter) == []
    assert _feature_postings(adapter) == []
    assert adapter.query(sparse_query_vector={"rare": 1.0}) == []
    assert adapter.retrieval_collection().get_meta_data()["RetrievalCapabilities"]["sparse"] is True


@pytest.mark.parametrize("engine", ["fts", "bm25"])
@pytest.mark.parametrize("damage", ["drop", "replace_wrong_definition"])
def test_database_fulltext_readiness_rejects_physical_index_damage(
    feature_adapter, feature_peer, engine, damage
):
    adapter = feature_adapter
    if adapter.probe_capabilities()[engine]["state"] != "available":
        pytest.skip(f"test instance does not advertise {engine}")
    collection = adapter.retrieval_collection()
    collection.configure_fulltext(engine)
    collection.migrate_content_schema(allow_ddl=True)
    assert collection.backfill_content_batch(lambda row: "retrieval " + row["id"])["done"]
    collection.build_fulltext_index(allow_ddl=True)
    assert collection.validate_fulltext_index(engine) is True
    assert collection.get_meta_data()["RetrievalCapabilities"][engine] is True
    metadata = deepcopy(_feature_metadata(adapter))
    index = metadata["_retrieval"]["index"]["name"]
    assert index.startswith(adapter.collection_name + "_ov_")
    # Change only this fixture's physical index, deliberately leaving metadata ready.
    _feature_sql(feature_peer, f'DROP INDEX "{index}"')
    if damage == "replace_wrong_definition":
        _feature_sql(
            feature_peer,
            f'CREATE INDEX "{index}" ON "{adapter.collection_name}" USING btree (uri)',
        )
    assert _feature_metadata(adapter) == metadata
    assert collection.validate_fulltext_index(engine) is False
    assert collection.get_meta_data()["RetrievalCapabilities"][engine] is False
    assert feature_peer.retrieval_collection().get_meta_data()["RetrievalCapabilities"][engine] is False
    with pytest.raises(RuntimeError, match="ready"):
        adapter.search_by_keywords(query="retrieval", mode=engine)


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
            cursor.execute(
                "SELECT pg_catalog.set_config('application_name', %s, true), pg_backend_pid()",
                (f"ov_feature_worker_{worker}",),
            )
            _, backend = cursor.fetchone()
            barrier.wait(timeout=10)
            cursor.execute(
                f"SELECT current_setting('application_name'), pg_backend_pid(), id, account_id "
                f'FROM "{adapter.collection_name}" WHERE id = %s', (row_id,),
            )
            assert cursor.fetchall() == [
                (f"ov_feature_worker_{worker}", backend, row_id, "b" if row_id == "private" else "a")
            ]
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
    adapter.retrieval_collection().create_sparse_index(sparse_weight=0.5)
    workers = adapter._connection_pool_max_size + 4
    barrier = threading.Barrier(workers)
    queries = [
        {"sparse_query_vector": {"rare": 1.0}},
        {"query_vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
        {"query_vector": [1.0, 0, 0, 0, 0, 0, 0, 0],
         "sparse_query_vector": {"rare": 1.0}},
    ]
    expected = {}
    for account in ("a", "b"):
        for kind, query in enumerate(queries):
            hits = adapter.query(
                **query, filter={"op": "must", "field": "account_id", "conds": [account]}
            )
            assert {hit["id"] for hit in hits} == ({"old", "new"} if account == "a" else {"private"})
            expected[account, kind] = [(hit["id"], hit["_score"]) for hit in hits]

    def query_repeatedly(worker):
        barrier.wait(timeout=10)
        for iteration in range(8):
            account = "a" if (worker + iteration) % 2 else "b"
            kind = (worker + iteration) % len(queries)
            hits = adapter.query(
                **queries[kind],
                filter={"op": "must", "field": "account_id", "conds": [account]},
            )
            assert [hit["id"] for hit in hits] == [key for key, _ in expected[account, kind]]
            assert [hit["_score"] for hit in hits] == pytest.approx(
                [score for _, score in expected[account, kind]]
            )
            assert all(hit["account_id"] == account for hit in hits)
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(query_repeatedly, worker) for worker in range(workers)]
        assert all(future.result(timeout=45) for future in futures)
    assert _feature_sql(adapter, "SELECT 1") == [(1,)]
