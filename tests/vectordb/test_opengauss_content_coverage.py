# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Content coverage regressions, with a strict offline transaction harness.

Set OPENVIKING_CONTENT_COVERAGE_DOCKER=1 to also use random schemas in the
explicitly approved ov-retrieval-acceptance-20261016 container on port 35436.
Credentials are read in memory from that container and are never logged.
"""

import json
import os
import re
import subprocess
import threading
import uuid
from copy import deepcopy
from unittest.mock import Mock

import pytest

from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection

METHODS = ("upsert_data", "update_data")
COLUMNS = {
    "id": "character varying",
    "uri": "text",
    "updated_at": "bigint",
    "content": "text",
    "_ov_content_empty": "boolean",
}


def metadata(state="ready"):
    return {
        "Fields": [
            {"FieldName": "id", "FieldType": "string"},
            {"FieldName": "uri", "FieldType": "string"},
            {"FieldName": "updated_at", "FieldType": "int64"},
            {"FieldName": "content", "FieldType": "text"},
        ],
        "_retrieval": {"schema_ready": True, "schema_version": 2, "state": state},
    }


def make_collection(conn, meta=None):
    # Bypass ANN catalog initialization, not any content/transaction method.
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "coverage_rows"
    collection._conn = conn
    collection._lock = threading.RLock()
    collection._meta = deepcopy(meta if meta is not None else metadata())
    collection._dim = 0
    collection._distributed = False
    collection._field_names = {"id", "uri", "updated_at", "content"}
    collection._array_fields = set()
    collection._date_time_fields = set()
    collection._materialize_pending_indexes = Mock()
    return collection


class UniqueViolation(RuntimeError):
    pgcode = "23505"


class TransactionHarness:
    """Execute only the emitted write SQL; reject unknown statements immediately.

    Empty TEXT is normalized to NULL, as on the acceptance database's A mode.
    Committed and working rows are separate, so rollback assertions observe data
    rather than merely counting calls to connection.rollback().
    """

    def __init__(self):
        self.types = dict(COLUMNS)
        self.meta = metadata()
        self.rows = {}
        self.working = None
        self.working_meta = None
        self.saved_types = None
        self.result = None
        self.events = []
        self.conn = Mock()
        self.cursor = self.conn.cursor.return_value
        self.cursor.execute.side_effect = self.execute
        self.cursor.fetchone.side_effect = lambda: self.result
        self.conn.commit.side_effect = self.commit
        self.conn.rollback.side_effect = self.rollback
        self.collection = make_collection(self.conn)
        self.collection._get_all_columns = Mock(side_effect=lambda: list(self.types))
        self.collection._get_column_types = Mock(side_effect=lambda: dict(self.types))

    def begin(self):
        if self.working is None:
            self.working = deepcopy(self.rows)
            self.working_meta = deepcopy(self.meta)
            self.saved_types = dict(self.types)

    def commit(self):
        self.events.append(("COMMIT", None))
        if self.working is not None:
            self.rows = self.working
            self.meta = self.working_meta
        self.working = self.working_meta = self.saved_types = None

    def rollback(self):
        self.events.append(("ROLLBACK", None))
        if self.saved_types is not None:
            self.types = self.saved_types
        self.working = self.working_meta = self.saved_types = None

    def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        self.events.append((sql, deepcopy(params)))
        self.begin()
        self.result = None
        self.cursor.rowcount = 0
        if sql.startswith('LOCK TABLE "coverage_rows"'):
            assert sql.endswith(("IN ROW EXCLUSIVE MODE", "IN SHARE MODE"))
        elif sql.startswith('SELECT meta_json FROM "_ov_collection_meta"'):
            assert params == ("coverage_rows",)
            assert sql.endswith(("FOR SHARE", "FOR UPDATE"))
            self.result = (
                (json.dumps(self.working_meta),) if self.working_meta is not None else None
            )
        elif sql.startswith('UPDATE "_ov_collection_meta"'):
            assert params[1] == "coverage_rows"
            self.working_meta = json.loads(params[0])
            self.cursor.rowcount = 1
        elif sql.startswith('UPDATE "coverage_rows" SET '):
            row = self.working.get(str(params[-1]))
            if row is not None:
                assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
                fields = re.findall(r'"([a-z_]+)" = %s', assignments)
                assert len(fields) == len(params) - 1
                row.update(
                    {
                        key: None if value == "" else value
                        for key, value in zip(fields, params[:-1], strict=True)
                    }
                )
                self.cursor.rowcount = 1
        elif sql.startswith('INSERT INTO "coverage_rows"'):
            fields = re.findall(r'"([a-z_]+)"', sql.split("VALUES", 1)[0])[1:]
            assert len(fields) == len(params)
            row = {name: False if name == "_ov_content_empty" else None for name in self.types}
            row.update(
                {
                    key: None if value == "" else value
                    for key, value in zip(fields, params, strict=True)
                }
            )
            if row["id"] in self.working:
                raise UniqueViolation("duplicate coverage id")
            self.working[row["id"]] = row
            self.cursor.rowcount = 1
        elif sql.startswith('SELECT "id" FROM "coverage_rows" WHERE '):
            assert '"content" IS NULL' in sql
            assert sql.endswith("LIMIT 1")
            assert sql.count("%s") == len(params)
            guard = 'NOT "_ov_content_empty"' in sql
            for record_id in params:
                row = self.working.get(record_id)
                if (
                    row
                    and row.get("content") is None
                    and not (guard and row.get("_ov_content_empty"))
                ):
                    self.result = (record_id,)
                    break
        elif sql.startswith('ALTER TABLE "coverage_rows" ADD COLUMN '):
            if '"content" TEXT' in sql:
                self.types["content"] = "text"
                for row in self.working.values():
                    row["content"] = None
            else:
                assert '"_ov_content_empty" BOOLEAN NOT NULL DEFAULT FALSE' in sql
                self.types["_ov_content_empty"] = "boolean"
                for row in self.working.values():
                    row["_ov_content_empty"] = False
        elif sql == "SAVEPOINT ov_upsert_insert":
            self.savepoint_rows = deepcopy(self.working)
        elif sql == "ROLLBACK TO SAVEPOINT ov_upsert_insert":
            self.working = deepcopy(self.savepoint_rows)
        elif sql == "RELEASE SAVEPOINT ov_upsert_insert":
            self.savepoint_rows = None
        else:
            raise AssertionError(f"Unhandled coverage SQL: {sql}")

    def seed(self, *records):
        for record in records:
            row = {name: False if name == "_ov_content_empty" else None for name in self.types}
            row.update(deepcopy(record))
            if row.get("content") == "":
                row["content"] = None
            self.rows[row["id"]] = row

    def snapshot(self):
        return deepcopy(self.rows)

    def set_state(self, state):
        self.meta = metadata(state)


class DatabaseHarness:
    def __init__(self, conn):
        self.conn = conn
        self.collection = make_collection(conn)

    def sql(self, statement, params=None):
        cursor = self.conn.cursor()
        try:
            cursor.execute(statement, params)
            result = cursor.fetchall() if cursor.description else []
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def seed(self, *records):
        for record in records:
            fields = ", ".join(f'"{name}"' for name in record)
            self.sql(
                f'INSERT INTO "coverage_rows" ({fields}) VALUES ({", ".join(["%s"] * len(record))})',
                list(record.values()),
            )

    def snapshot(self):
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM "coverage_rows" ORDER BY id')
            fields = [item[0] for item in cursor.description]
            rows = [dict(zip(fields, row, strict=True)) for row in cursor.fetchall()]
            self.conn.commit()
            return {row["id"]: row for row in rows}
        finally:
            cursor.close()

    def set_state(self, state):
        self.sql('UPDATE "_ov_collection_meta" SET meta_json = %s', (json.dumps(metadata(state)),))


@pytest.fixture(scope="module")
def docker_password():
    if os.getenv("OPENVIKING_CONTENT_COVERAGE_DOCKER") != "1":
        pytest.skip("set OPENVIKING_CONTENT_COVERAGE_DOCKER=1 for the approved disposable container")
    container = json.loads(subprocess.check_output(
        ["docker", "inspect", "ov-retrieval-acceptance-20261016"], timeout=10,
    ))[0]
    ports = container["NetworkSettings"]["Ports"].get("5432/tcp", [])
    if not any(port["HostIp"] == "127.0.0.1" and port["HostPort"] == "35436" for port in ports):
        pytest.fail("approved acceptance container does not expose the expected loopback port")
    env = dict(item.split("=", 1) for item in container["Config"]["Env"])
    return env["GS_PASSWORD"]


@pytest.fixture(
    params=[
        "mock-a",
        pytest.param("database-a", marks=pytest.mark.integration),
        pytest.param("database-pg", marks=pytest.mark.integration),
    ]
)
def coverage(request):
    if request.param == "mock-a":
        yield TransactionHarness()
        return
    password = request.getfixturevalue("docker_password")
    psycopg2 = pytest.importorskip("psycopg2")
    database = "postgres" if request.param == "database-a" else "ov_acceptance_pg"
    conn = psycopg2.connect(
        host="127.0.0.1", port=35436, user="gaussdb", password=password,
        dbname=database, connect_timeout=5,
    )
    harness = DatabaseHarness(conn)
    schema = "ov_content_coverage_" + uuid.uuid4().hex[:12]
    created = False
    try:
        compatibility = harness.sql("SHOW sql_compatibility")[0][0]
        assert compatibility.upper() == ("A" if request.param == "database-a" else "PG")
        harness.sql("SET statement_timeout = 5000")
        harness.sql(f'CREATE SCHEMA "{schema}"')
        created = True
        harness.sql(f'SET search_path TO "{schema}"')
        harness.sql('CREATE TABLE "_ov_collection_meta" (table_name VARCHAR(256) PRIMARY KEY, meta_json TEXT NOT NULL)')
        harness.sql('INSERT INTO "_ov_collection_meta" VALUES (%s, %s)', ("coverage_rows", json.dumps(metadata())))
        harness.sql('CREATE TABLE "coverage_rows" (id VARCHAR(256) PRIMARY KEY, uri TEXT, updated_at BIGINT, content TEXT, "_ov_content_empty" BOOLEAN NOT NULL DEFAULT FALSE)')
        yield harness
    finally:
        conn.rollback()
        try:
            if created:
                harness.sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            conn.close()


def assert_rejected_atomically(harness, method, records, match="coverage"):
    before = harness.snapshot()
    original = deepcopy(records)
    with pytest.raises(ValueError, match=match):
        getattr(harness.collection, method)(records)
    assert harness.snapshot() == before
    assert records == original
    if isinstance(harness, TransactionHarness):
        harness.conn.commit.assert_not_called()
        harness.conn.rollback.assert_called_once()
        harness.cursor.close.assert_called_once()
        harness.collection._materialize_pending_indexes.assert_not_called()


@pytest.mark.parametrize("record", [{"id": "new"}, {"id": "new", "content": None}])
def test_ready_upsert_rejects_new_record_without_content(coverage, record):
    assert_rejected_atomically(coverage, "upsert_data", [record])


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("previous", ["正文", ""])
def test_ready_rejects_explicit_null_even_after_legal_empty(coverage, method, previous):
    coverage.seed({"id": "a", "content": previous, "_ov_content_empty": previous == ""})
    assert_rejected_atomically(coverage, method, [{"id": "a", "content": None}])


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("empty", [False, True])
def test_partial_update_preserves_existing_content_and_empty_marker(coverage, method, empty):
    coverage.seed({"id": "a", "content": "" if empty else "原文", "_ov_content_empty": empty})
    before = coverage.snapshot()["a"]
    records = [{"id": "a", "uri": "viking://updated", "updated_at": 42}]
    original = deepcopy(records)
    getattr(coverage.collection, method)(records)
    row = coverage.snapshot()["a"]
    assert row == {**before, "uri": "viking://updated", "updated_at": 42}
    assert records == original


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("patch", [{"id": "a"}, {"id": "a", "uri": "changed"}])
def test_partial_update_of_missing_content_is_rejected(coverage, method, patch):
    coverage.seed({"id": "a", "uri": "before"})
    assert_rejected_atomically(coverage, method, [patch])


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("text", ["", " ", "中文正文 retrieval"])
def test_legal_text_and_empty_are_accepted_without_mutating_input(coverage, method, text):
    coverage.seed({"id": "a", "content": "before"})
    records = [{"id": "a", "content": text}]
    original = deepcopy(records)
    getattr(coverage.collection, method)(records)
    row = coverage.snapshot()["a"]
    assert row["_ov_content_empty"] is (text == "")
    if text == "":
        assert row["content"] in (None, "")
    else:
        assert row["content"] == text
    assert records == original


@pytest.mark.parametrize("method", METHODS)
def test_replacing_legal_empty_with_nonempty_clears_marker(coverage, method):
    coverage.seed({"id": "a", "content": "", "_ov_content_empty": True})
    getattr(coverage.collection, method)([{"id": "a", "content": "restored"}])
    row = coverage.snapshot()["a"]
    assert row["content"] == "restored"
    assert row["_ov_content_empty"] is False


def test_upsert_new_legal_empty_is_covered(coverage):
    coverage.collection.upsert_data([{"id": "new", "content": ""}])
    assert coverage.snapshot()["new"]["_ov_content_empty"] is True


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("bad_first", [False, True])
def test_mixed_batch_coverage_failure_rolls_back_every_row(coverage, method, bad_first):
    coverage.seed({"id": "a", "content": "original", "uri": "before"}, {"id": "b", "content": "original"})
    records = [{"id": "a", "content": "modified", "uri": "changed"}, {"id": "b", "content": None}]
    assert_rejected_atomically(coverage, method, records[::-1] if bad_first else records)


@pytest.mark.parametrize("bad_first", [False, True])
def test_failed_upsert_batch_removes_preceding_and_following_inserts(coverage, bad_first):
    records = [{"id": "good", "content": "new"}, {"id": "bad", "uri": "missing"}]
    assert_rejected_atomically(coverage, "upsert_data", records[::-1] if bad_first else records)


@pytest.mark.parametrize("method", METHODS)
def test_only_written_ids_are_checked_not_unrelated_legacy_holes(coverage, method):
    coverage.seed({"id": "legacy"}, {"id": "a", "content": "covered"})
    getattr(coverage.collection, method)([{"id": "a", "uri": "changed"}])
    assert coverage.snapshot()["legacy"]["content"] is None
    assert coverage.snapshot()["a"]["uri"] == "changed"


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("state", ["schema_ready", "backfilling", "backfilled", "configured", None])
def test_migration_states_allow_missing_and_null_content(coverage, method, state):
    coverage.set_state(state)
    coverage.seed({"id": "a", "content": "before"}, {"id": "b"})
    getattr(coverage.collection, method)([{"id": "a", "content": None}, {"id": "b", "uri": "changed"}])
    assert coverage.snapshot()["a"]["content"] is None
    assert coverage.snapshot()["a"]["_ov_content_empty"] is False
    assert coverage.snapshot()["b"]["uri"] == "changed"


@pytest.mark.parametrize("method", METHODS)
def test_stale_unready_handle_obeys_database_ready_metadata(coverage, method):
    coverage.collection._meta = metadata("backfilling")
    coverage.seed({"id": "a", "content": "before"})
    assert_rejected_atomically(coverage, method, [{"id": "a", "content": None}])


@pytest.mark.parametrize("method", METHODS)
def test_stale_ready_handle_obeys_database_migration_metadata(coverage, method):
    coverage.set_state("backfilling")
    coverage.seed({"id": "a", "content": "before"})
    getattr(coverage.collection, method)([{"id": "a", "content": None}])
    assert coverage.snapshot()["a"]["content"] is None


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("invalid", [0, False, [], {}, b"text", 1.25])
def test_invalid_content_type_is_rejected_before_any_batch_mutation(coverage, method, invalid):
    coverage.seed({"id": "a", "content": "original"}, {"id": "b", "content": "original"})
    before = coverage.snapshot()
    records = [{"id": "a", "content": "changed"}, {"id": "b", "content": invalid}]
    original = deepcopy(records)
    with pytest.raises(ValueError, match="content must be"):
        getattr(coverage.collection, method)(records)
    assert coverage.snapshot() == before
    assert records == original
    if isinstance(coverage, TransactionHarness):
        assert coverage.events == []


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("flag", [True, False, None])
def test_caller_cannot_supply_internal_empty_marker(coverage, method, flag):
    coverage.seed({"id": "a", "content": "original"}, {"id": "b", "content": "original"})
    before = coverage.snapshot()
    records = [{"id": "a", "content": "changed"}, {"id": "b", "content": None, "_ov_content_empty": flag}]
    with pytest.raises(ValueError, match="internal"):
        getattr(coverage.collection, method)(records)
    assert coverage.snapshot() == before
    if isinstance(coverage, TransactionHarness):
        assert coverage.events == []


@pytest.mark.parametrize("method", METHODS)
def test_lock_order_and_coverage_query_share_the_write_transaction(method):
    harness = TransactionHarness()
    harness.seed({"id": "a", "content": "original"})
    getattr(harness.collection, method)([{"id": "a", "content": "changed"}])
    statements = [sql for sql, _ in harness.events]
    assert statements[0] == 'LOCK TABLE "coverage_rows" IN ROW EXCLUSIVE MODE'
    assert statements[1].endswith("FOR SHARE")
    assert statements[2].startswith('UPDATE "coverage_rows"')
    assert statements[3].startswith('SELECT "id" FROM "coverage_rows"')
    assert 'AND "content" IS NULL AND NOT "_ov_content_empty" LIMIT 1' in statements[3]
    assert harness.events[3][1] == ["a"]
    assert statements[4:] == ["COMMIT"]
    harness.cursor.close.assert_called_once()


@pytest.mark.parametrize("method", METHODS)
def test_missing_authoritative_metadata_aborts_before_base_mutation(method):
    harness = TransactionHarness()
    harness.meta = None
    with pytest.raises(RuntimeError, match="metadata is missing"):
        getattr(harness.collection, method)([{"id": "a", "content": "text"}])
    assert harness.snapshot() == {}
    harness.conn.commit.assert_not_called()
    harness.conn.rollback.assert_called_once()
    assert not any(sql.startswith(('UPDATE "coverage_rows"', 'INSERT INTO "coverage_rows"')) for sql, _ in harness.events)


@pytest.mark.parametrize("existing_content", [False, True])
def test_schema_migration_adds_nonnullable_empty_marker_and_publishes_v2(coverage, existing_content):
    collection = coverage.collection
    if isinstance(coverage, TransactionHarness):
        coverage.types.pop("_ov_content_empty")
        if not existing_content:
            coverage.types.pop("content")
    else:
        coverage.sql('ALTER TABLE "coverage_rows" DROP COLUMN "_ov_content_empty"')
        if not existing_content:
            coverage.sql('ALTER TABLE "coverage_rows" DROP COLUMN "content"')
    coverage.seed({"id": "legacy"})
    collection._meta = metadata("schema_ready")
    collection._meta["_retrieval"]["schema_version"] = 1
    if isinstance(coverage, TransactionHarness):
        coverage.meta = deepcopy(collection._meta)
    else:
        coverage.sql('UPDATE "_ov_collection_meta" SET meta_json = %s', (json.dumps(collection._meta),))
    state = collection.migrate_content_schema(allow_ddl=True)
    assert state["schema_version"] == 2
    assert state["schema_ready"] is True
    assert coverage.snapshot()["legacy"]["_ov_content_empty"] is False
    assert "_ov_content_empty" not in collection._field_names
    assert collection.migrate_content_schema(allow_ddl=True) == state
    if isinstance(coverage, DatabaseHarness):
        definition = coverage.sql(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'coverage_rows' "
            "AND column_name = '_ov_content_empty'",
        )[0]
        assert definition[0:2] == ("boolean", "NO")
        assert "false" in definition[2].lower()


def test_ready_promotion_between_schema_discovery_and_write_cannot_bypass_coverage(coverage, monkeypatch):
    collection = coverage.collection
    if isinstance(coverage, TransactionHarness):
        coverage.types.pop("content")
        coverage.types.pop("_ov_content_empty")
    else:
        coverage.sql('ALTER TABLE "coverage_rows" DROP COLUMN "content"')
        coverage.sql('ALTER TABLE "coverage_rows" DROP COLUMN "_ov_content_empty"')
    collection._meta = metadata("configured")
    collection._meta["_retrieval"]["schema_ready"] = False
    if isinstance(coverage, TransactionHarness):
        coverage.meta = deepcopy(collection._meta)
    else:
        coverage.sql('UPDATE "_ov_collection_meta" SET meta_json = %s', (json.dumps(collection._meta),))
    get_columns = collection._get_all_columns
    peer = make_collection(collection._conn, collection._meta)
    if isinstance(coverage, TransactionHarness):
        peer._get_column_types = collection._get_column_types
    before_write = {}

    def discover_then_finish_migration():
        columns = get_columns()
        assert "content" not in columns
        # Deterministic interleaving: another worker completes migration/build
        # after this writer's schema snapshot, but before its write transaction.
        peer.migrate_content_schema(allow_ddl=True)
        coverage.set_state("ready")
        before_write.update(coverage.snapshot())
        return columns

    monkeypatch.setattr(collection, "_get_all_columns", discover_then_finish_migration)
    error = None
    try:
        collection.upsert_data([{"id": "new", "uri": "missing source"}])
    except ValueError as caught:
        error = caught
    after_write = coverage.snapshot()
    assert after_write == before_write, (
        "Writer committed missing source content after ready promotion; "
        f"new row: {after_write.get('new')}"
    )
    assert error is not None and "coverage" in str(error)


@pytest.mark.parametrize("projection", [None, ["content"], ["uri"]])
def test_a_mode_empty_content_round_trips_without_exposing_internal_marker(coverage, projection):
    if isinstance(coverage, TransactionHarness):
        pytest.skip("projection requires real cursor descriptions and database empty-string semantics")
    coverage.collection.upsert_data([{"id": "a", "uri": "source", "content": ""}])
    if projection is None:
        item = coverage.collection.fetch_data(["a"]).items[0]
    else:
        item = coverage.collection.search_by_random("unused", output_fields=projection).data[0]
    assert "_ov_content_empty" not in item.fields
    if projection is None or "content" in projection:
        assert item.fields["content"] == ""
    else:
        assert "content" not in item.fields
