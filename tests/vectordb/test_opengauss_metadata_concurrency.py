# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Offline metadata CAS and lock-order regressions; no database connections."""

import hashlib
import threading
from copy import deepcopy

import pytest
from test_opengauss_content_coverage import TransactionHarness

from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection

EMPTY_HASH = hashlib.md5(b"").hexdigest()
META_LOCK = 'SELECT meta_json FROM "_ov_collection_meta" WHERE table_name = %s FOR UPDATE'
BASE_LOCK = 'LOCK TABLE "coverage_rows" IN ROW EXCLUSIVE MODE'
BACKFILL_UPDATE = (
    'UPDATE "coverage_rows" SET "content" = %s, "_ov_content_empty" = %s '
    'WHERE "id" = %s AND "updated_at" IS NOT DISTINCT FROM %s '
    'AND "content" IS NULL AND NOT "_ov_content_empty" '
    "AND md5(coalesce(\"content\", '')) IS NOT DISTINCT FROM %s"
)
MISSING_CONTENT = (
    'SELECT EXISTS (SELECT 1 FROM "coverage_rows" '
    'WHERE "content" IS NULL AND NOT "_ov_content_empty")'
)


class MetadataHarness(TransactionHarness):
    """Extend the coverage harness only for the exact batch-backfill SQL."""

    def __init__(self):
        super().__init__()
        self.meta["_retrieval"].update(
            state="backfilling",
            generation=0,
            checkpoint={"after_id": None, "upper_id": None},
        )
        self.collection._meta = deepcopy(self.meta)
        self.collection._execute = OpenGaussCollection._execute.__get__(self.collection)
        self.cursor.fetchall.side_effect = lambda: deepcopy(self.read_rows)
        self.read_rows = []
        self.seed({"id": "a", "uri": "viking://resources/a.txt", "updated_at": 7})

    def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        if sql not in {
            'SELECT "id" FROM "coverage_rows" ORDER BY "id" COLLATE "C" DESC LIMIT 1',
            'SELECT "id", "updated_at", "uri", md5(coalesce("content", \'\')) '
            'FROM "coverage_rows" WHERE "content" IS NULL AND NOT "_ov_content_empty" '
            'AND "id" COLLATE "C" <= %s ORDER BY "id" COLLATE "C" LIMIT %s',
            BACKFILL_UPDATE,
            MISSING_CONTENT,
        }:
            return super().execute(statement, params)
        self.events.append((sql, deepcopy(params)))
        self.begin()
        self.result = None
        self.cursor.rowcount = 0
        if sql.startswith('SELECT "id" FROM'):
            assert params is None
            self.read_rows = [(max(self.working),)] if self.working else []
        elif sql.startswith('SELECT "id",'):
            assert params == ["a", 100]
            self.read_rows = [
                (row["id"], row["updated_at"], row["uri"], EMPTY_HASH)
                for row in self.working.values()
                if row["content"] is None and not row["_ov_content_empty"]
            ]
        elif sql == BACKFILL_UPDATE:
            transaction_sql = [event[0] for event in self.events]
            last_commit = max(
                (i for i, event in enumerate(transaction_sql) if event == "COMMIT"),
                default=-1,
            )
            assert transaction_sql[last_commit + 1 : last_commit + 3] == [BASE_LOCK, META_LOCK]
            text, empty, record_id, updated_at, old_hash = params
            row = self.working[record_id]
            if (
                row["content"] is None
                and not row["_ov_content_empty"]
                and row["updated_at"] == updated_at
                and old_hash == EMPTY_HASH
            ):
                row.update(content=None if text == "" else text, _ov_content_empty=empty)
                self.cursor.rowcount = 1
        else:
            assert params is None
            self.result = (
                any(
                    row["content"] is None and not row["_ov_content_empty"]
                    for row in self.working.values()
                ),
            )


def test_operation_metadata_refresh_cannot_authorize_stale_state_writeback():
    harness = MetadataHarness()
    collection = harness.collection
    s0 = deepcopy(collection._meta["_retrieval"])
    s1 = deepcopy(harness.meta)
    s1["_retrieval"].update(generation=1, sparse_enabled=True, sparse_ready=True)

    def operation(cursor, state):
        assert cursor is harness.cursor
        assert state == s0
        state["checkpoint"]["after_id"] = "stale-cursor"
        harness.meta = deepcopy(s1)
        collection._adopt_metadata(deepcopy(s1))
        assert collection._meta["_retrieval"] == s1["_retrieval"]

    with pytest.raises(RuntimeError, match="Stale retrieval state"):
        collection._retrieval_transaction(operation)

    assert harness.meta == s1
    assert collection._meta == s1
    assert harness.events == [(META_LOCK, ("coverage_rows",)), ("ROLLBACK", None)]
    harness.conn.commit.assert_not_called()
    harness.conn.rollback.assert_called_once_with()
    harness.cursor.close.assert_called_once_with()


def test_operation_nested_changes_do_not_mutate_expected_state():
    harness = MetadataHarness()
    collection = harness.collection
    before = deepcopy(collection._meta)

    def operation(cursor, state):
        state["checkpoint"]["after_id"] = "a"
        assert collection._meta == before
        assert harness.meta == before
        return "saved"

    assert collection._retrieval_transaction(operation) == "saved"
    assert harness.meta["_retrieval"]["checkpoint"]["after_id"] == "a"
    assert collection._meta == harness.meta
    harness.conn.commit.assert_called_once_with()
    harness.conn.rollback.assert_not_called()
    harness.cursor.close.assert_called_once_with()


@pytest.mark.parametrize("refresh_local", [False, True])
def test_loader_remote_state_change_is_rejected_before_any_row_update(refresh_local):
    harness = MetadataHarness()
    collection = harness.collection
    before_rows = harness.snapshot()
    before_meta = deepcopy(collection._meta)
    s1 = deepcopy(harness.meta)
    s1["_retrieval"].update(generation=1, checkpoint={"after_id": "peer", "upper_id": "z"})
    write_start = []

    def loader(snapshot):
        assert snapshot == {"id": "a", "updated_at": 7, "uri": "viking://resources/a.txt"}
        assert harness.working is None
        harness.meta = deepcopy(s1)
        if refresh_local:
            collection._adopt_metadata(deepcopy(s1))
        write_start.append(len(harness.events))
        return "source body"

    with pytest.raises(RuntimeError, match="Stale retrieval state"):
        collection.backfill_content_batch(loader)

    assert len(write_start) == 1
    assert harness.events[write_start[0] :] == [
        (BASE_LOCK, None),
        (META_LOCK, ("coverage_rows",)),
        ("ROLLBACK", None),
    ]
    assert harness.snapshot() == before_rows
    assert harness.meta == s1
    assert collection._meta == (s1 if refresh_local else before_meta)
    assert harness.conn.commit.call_count == 2  # Only the two snapshot reads committed.
    harness.conn.rollback.assert_called_once_with()
    assert harness.cursor.close.call_count == 3


def test_backfill_locks_base_then_metadata_before_updating_rows_and_commits_once():
    harness = MetadataHarness()
    write_start = []

    def loader(snapshot):
        assert snapshot["id"] == "a"
        assert harness.working is None
        write_start.append(len(harness.events))
        return "source body"

    result = harness.collection.backfill_content_batch(loader)

    writes = harness.events[write_start[0] :]
    assert [sql for sql, _ in writes] == [
        BASE_LOCK,
        META_LOCK,
        BACKFILL_UPDATE,
        MISSING_CONTENT,
        META_LOCK,
        'UPDATE "_ov_collection_meta" SET meta_json = %s WHERE table_name = %s',
        "COMMIT",
    ]
    assert writes[1][1] == writes[4][1] == ("coverage_rows",)
    assert writes[2][1] == ["source body", False, "a", 7, EMPTY_HASH]
    assert result == {
        "updated": 1,
        "done": True,
        "checkpoint": {"after_id": None, "upper_id": None},
    }
    assert harness.snapshot()["a"]["content"] == "source body"
    assert harness.meta == harness.collection._meta
    assert harness.meta["_retrieval"]["backfilled_rows"] == 1
    assert harness.conn.commit.call_count == 3
    harness.conn.rollback.assert_not_called()
    assert harness.cursor.close.call_count == 3


class ObservedRLock:
    def __init__(self):
        self.lock = threading.RLock()
        self.blocked = threading.Event()

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.blocked.set()
            self.lock.acquire()
        return self

    def __exit__(self, *exc):
        self.lock.release()


def test_metadata_adoption_blocks_until_collection_lock_is_released():
    harness = MetadataHarness()
    collection = harness.collection
    before = deepcopy(collection._meta)
    fresh = deepcopy(before)
    fresh["_dim"] = 3
    fresh["_retrieval"]["generation"] = 1
    fresh["Fields"].extend(
        [
            {"FieldName": "tags", "FieldType": "list<string>"},
            {"FieldName": "changed_at", "FieldType": "date_time"},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
        ]
    )
    collection._lock = ObservedRLock()
    finished = threading.Event()
    errors = []

    def adopt():
        try:
            collection._adopt_metadata(fresh)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=adopt, daemon=True)
    try:
        with collection._lock:
            collection._adopt_metadata(deepcopy(before))
            worker.start()
            assert collection._lock.blocked.wait(timeout=2), (
                "adoption did not attempt the held lock"
            )
            assert not finished.is_set()
            assert collection._meta == before
            assert collection._dim == 0
            assert "tags" not in collection._field_names
    finally:
        if worker.ident is not None:
            worker.join(timeout=2)

    assert not worker.is_alive(), "metadata adoption did not resume after unlock"
    assert finished.is_set()
    assert not errors
    assert collection._meta == fresh
    assert collection._dim == 3
    assert {"tags", "changed_at", "sparse_vector", "vector"} <= collection._field_names
    assert collection._array_fields == {"tags"}
    assert collection._date_time_fields == {"changed_at"}
    assert collection._sparse_maintenance_checks is True
    harness.conn.cursor.assert_not_called()
