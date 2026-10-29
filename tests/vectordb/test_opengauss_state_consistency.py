"""Offline transaction and stale-handle regressions; no database is contacted."""

import json
import threading
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    OpenGaussCollectionAdapter,
    _save_collection_meta,
)
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig


@pytest.fixture
def collection():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._name = "context"
    collection._dim = 2
    collection._distributed = False
    collection._lock = threading.RLock()
    collection._meta = {
        "Description": "stale",
        "Fields": [{"FieldName": "id", "FieldType": "string"}],
    }
    collection._field_names = {"id", "vector"}
    collection._array_fields = set()
    collection._date_time_fields = set()
    collection._sparse_maintenance_checks = False
    collection._indexes = {"default": {"old": True}}
    collection._pending_indexes = {"pending": {"_state": "pending"}}
    collection._conn = Mock()
    cursor = collection._conn.cursor.return_value
    cursor.rowcount = 1
    cursor.fetchone.return_value = (json.dumps(collection._meta),)
    cursor.fetchall.return_value = []
    collection._get_all_columns = Mock(return_value=["id", "vector"])
    collection._get_column_types = Mock(return_value={"vector": "USER-DEFINED"})
    collection._materialize_pending_indexes = Mock()
    return collection


def sql_calls(collection):
    return collection._conn.cursor.return_value.execute.call_args_list


def latest_metadata(collection):
    calls = [
        call
        for call in sql_calls(collection)
        if call.args[0].startswith('UPDATE "_ov_collection_meta"')
    ]
    return json.loads(calls[-1].args[1][0])


@pytest.mark.parametrize("schema_update", [False, True])
def test_metadata_update_merges_locked_database_state(collection, schema_update):
    fresh = {
        "Description": "fresh",
        "Fields": [
            {"FieldName": "id", "FieldType": "string"},
            {"FieldName": "remote", "FieldType": "string"},
        ],
        "_sparse_index": {"candidate_limit": 128, "sparse_ready": True},
        "other": "preserved",
    }
    cursor = collection._conn.cursor.return_value
    cursor.fetchone.return_value = (json.dumps(fresh),)
    persisted = deepcopy(fresh)
    pending = {}
    before = deepcopy(collection._meta)

    def execute(sql, params=None):
        if sql.startswith('UPDATE "_ov_collection_meta"'):
            pending.update(json.loads(params[0]))

    def commit():
        assert collection._meta == before
        assert persisted == fresh
        persisted.update(pending)

    cursor.execute.side_effect = execute
    collection._conn.commit.side_effect = commit
    fields = (
        [{"FieldName": "sparse_vector", "FieldType": "sparse_vector"}] if schema_update else None
    )
    collection.update(fields=fields, description="new")
    saved = latest_metadata(collection)
    assert saved["_sparse_index"] == fresh["_sparse_index"]
    assert saved["other"] == "preserved"
    assert saved["Description"] == "new"
    assert {field["FieldName"] for field in saved["Fields"]} >= {"id", "remote"}
    assert any("FOR UPDATE" in call.args[0] for call in sql_calls(collection))
    if schema_update:
        assert sql_calls(collection)[0].args[0].startswith("LOCK TABLE")
        assert collection._sparse_maintenance_checks is True
    collection._conn.commit.assert_called_once()
    collection._conn.rollback.assert_not_called()
    assert collection._meta == saved == persisted


@pytest.mark.parametrize("failure", ["missing", "ddl", "write", "commit", "rowcount"])
def test_metadata_failure_rolls_back_without_publishing(collection, failure):
    cursor = collection._conn.cursor.return_value
    before = deepcopy(collection._meta)
    if failure == "missing":
        cursor.fetchone.return_value = None
    elif failure == "commit":
        collection._conn.commit.side_effect = RuntimeError("commit")
    elif failure == "rowcount":
        cursor.rowcount = 0
    else:

        def execute(sql, params=None):
            if sql.startswith("ALTER TABLE" if failure == "ddl" else "UPDATE"):
                raise RuntimeError(failure)

        cursor.execute.side_effect = execute
    with pytest.raises(RuntimeError):
        collection.update(fields=[{"FieldName": "sparse_vector", "FieldType": "sparse_vector"}])
    assert collection._meta == before
    assert collection._field_names == {"id", "vector"}
    assert collection._sparse_maintenance_checks is False
    collection._conn.rollback.assert_called_once()
    cursor.close.assert_called_once()


def test_schema_update_uses_fresh_definitions_and_rejects_type_changes(collection):
    fresh = {"Fields": [{"FieldName": "remote", "FieldType": "int64"}]}
    collection._conn.cursor.return_value.fetchone.return_value = (json.dumps(fresh),)
    with pytest.raises(ValueError, match="explicit migration"):
        collection.update(fields=[{"FieldName": "remote", "FieldType": "string"}])
    assert not any(call.args[0].startswith("ALTER TABLE") for call in sql_calls(collection))
    collection._conn.rollback.assert_called_once()


@pytest.mark.parametrize("method", ["upsert_data", "update_data"])
@pytest.mark.parametrize("physical_content", [False, True])
def test_content_writes_use_fixed_physical_columns(collection, method, physical_content):
    columns = ["id", "vector"] + (["content"] if physical_content else [])
    collection._get_all_columns.return_value = columns
    collection._get_column_types.return_value = {"content": "text"}
    records = [{"id": "a", "content": "正文"}, {"id": "b", "content": "second"}]
    getattr(collection, method)(records)
    updates = [
        call for call in sql_calls(collection) if call.args[0].startswith('UPDATE "context"')
    ]
    assert len(updates) == 2
    if not physical_content and method == "upsert_data":
        inserts = [
            call
            for call in sql_calls(collection)
            if call.args[0].startswith('INSERT INTO "context"')
        ]
        assert inserts == []
        assert all('"content"' not in call.args[0] for call in updates)
    for call in updates:
        assert ('"content"' in call.args[0]) is physical_content
    if physical_content:
        assert [call.args[1][0] for call in updates] == ["正文", "second"]
    collection._get_all_columns.assert_called_once()
    collection._conn.commit.assert_called_once()


@pytest.mark.parametrize("method", ["upsert_data", "update_data", "delete_data", "delete_all_data"])
def test_stale_handle_maintains_database_enabled_sparse(collection, method):
    cursor = collection._conn.cursor.return_value
    collection._get_all_columns.return_value = ["id", "vector", "sparse_vector"]
    collection._get_column_types.return_value = {"sparse_vector": "jsonb"}
    meta = {"_sparse_index": {"sparse_enabled": True, "sparse_ready": True}}

    def execute(sql, params=None):
        if "information_schema.columns" in sql:
            cursor.fetchone.return_value = (True,)
        elif sql.startswith("SELECT meta_json"):
            cursor.fetchone.return_value = (json.dumps(meta),)
        else:
            cursor.fetchone.return_value = None

    cursor.execute.side_effect = execute
    args = (
        []
        if method == "delete_all_data"
        else [["a"]]
        if method == "delete_data"
        else [[{"id": "a"}]]
    )
    getattr(collection, method)(*args)
    statements = [call.args[0] for call in sql_calls(collection)]
    assert any(sql.startswith('DELETE FROM "_ov_sparse_context"') for sql in statements)
    if method in {"upsert_data", "update_data"}:
        assert any(sql.startswith('INSERT INTO "_ov_sparse_context"') for sql in statements)
    collection._conn.commit.assert_called_once()
    collection._conn.rollback.assert_not_called()


@pytest.mark.parametrize("method", ["upsert_data", "update_data", "delete_data", "delete_all_data"])
def test_sparse_hook_failure_aborts_base_mutation(collection, method):
    collection._get_all_columns.return_value = ["id", "sparse_vector"]
    cursor = collection._conn.cursor.return_value

    def execute(sql, params=None):
        cursor.fetchone.return_value = (
            (json.dumps(collection._meta),) if sql.startswith("SELECT meta_json") else (True,)
        )

    cursor.execute.side_effect = execute
    collection._sync_sparse_postings_on_cursor = Mock(side_effect=RuntimeError("postings"))
    collection._delete_sparse_postings_on_cursor = Mock(side_effect=RuntimeError("postings"))
    args = (
        []
        if method == "delete_all_data"
        else [["a"]]
        if method == "delete_data"
        else [[{"id": "a"}]]
    )
    with pytest.raises(RuntimeError, match="postings"):
        getattr(collection, method)(*args)
    collection._conn.commit.assert_not_called()
    collection._conn.rollback.assert_called_once()
    collection._conn.cursor.return_value.close.assert_called_once()


def test_database_disabled_sparse_does_not_touch_postings(collection):
    collection._get_all_columns.return_value = ["id", "sparse_vector"]
    collection._conn.cursor.return_value.fetchone.return_value = (json.dumps({"_sparse_index": {}}),)
    collection.upsert_data([{"id": "a"}])
    assert not any("_ov_sparse_context" in call.args[0] for call in sql_calls(collection))


@pytest.mark.parametrize("fail_at", [None, 0, 1, 2, 3, "commit"])
def test_drop_is_atomic_and_does_not_depend_on_cached_sparse_state(collection, fail_at):
    cursor = collection._conn.cursor.return_value
    cursor.fetchone.return_value = (json.dumps({"_sparse_index": {"sparse_enabled": True}}),)
    if isinstance(fail_at, int):
        cursor.execute.side_effect = [None] * fail_at + [RuntimeError("drop")]
    elif fail_at == "commit":
        collection._conn.commit.side_effect = RuntimeError("drop")
    if fail_at is not None:
        with pytest.raises(RuntimeError, match="drop"):
            collection.drop()
        assert collection._indexes and collection._pending_indexes
        collection._conn.rollback.assert_called_once()
    else:
        collection.drop()
        statements = [call.args[0] for call in sql_calls(collection)]
        assert statements == [
            'DROP TABLE IF EXISTS "context" CASCADE',
            'SELECT meta_json FROM "_ov_collection_meta" WHERE table_name = %s',
            'DROP TABLE IF EXISTS "_ov_sparse_context"',
            'DROP TABLE IF EXISTS "_ov_index_context" CASCADE',
            'DELETE FROM "_ov_collection_meta" WHERE table_name = %s',
        ]
        collection._conn.commit.assert_called_once()
        assert not collection._indexes and not collection._pending_indexes
    cursor.close.assert_called_once()


@pytest.mark.parametrize("physical_content", [False, True])
def test_explicit_content_projection_checks_physical_columns(collection, physical_content):
    collection._get_all_columns.return_value = ["id"] + (["content"] if physical_content else [])
    expected = '"id", "content"' if physical_content else '"id", NULL AS "content"'
    assert collection._select_output_columns(["content"]) == expected
    collection._get_all_columns.assert_called_once()


@pytest.mark.parametrize("dimension", [None, 0, 8])
def test_metadata_refresh_adopts_schema_and_dimension(collection, dimension):
    fresh = {
        "Fields": [
            {"FieldName": "id", "FieldType": "string"},
            {"FieldName": "content", "FieldType": "text"},
            {"FieldName": "tags", "FieldType": "list<string>"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
        ],
        "Description": "database",
    }
    if dimension is not None:
        fresh["_dim"] = dimension
    collection._execute = Mock(return_value=[(json.dumps(fresh),)])
    collection._get_column_types.return_value = {field["FieldName"]: field["FieldType"] for field in fresh["Fields"]}
    meta = collection.get_meta_data()
    assert collection._meta == fresh
    assert meta["Fields"] == fresh["Fields"]
    assert "content" in {field["FieldName"] for field in meta["Fields"]}
    assert {"tags", "updated_at", "sparse_vector"} <= collection._field_names
    assert collection._array_fields == {"tags"}
    assert collection._date_time_fields == {"updated_at"}
    assert collection._sparse_maintenance_checks is True
    assert collection._dim == (2 if dimension is None else dimension)
    assert ("vector" in collection._field_names) is (dimension != 0)


def sparse_catalog():
    return [
        ("_ov_sparse_context_term_idx", True, True, "btree", False, False, 2, "term", "id", True),
        ("_ov_sparse_context_pkey", True, True, "btree", True, True, 2, "id", "term", True),
    ]


@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "missing_term",
        "missing_primary",
        "invalid_term",
        "unready_primary",
        "wrong_method",
        "wrong_term_keys",
        "wrong_primary_keys",
        "partial",
        "nonunique_primary",
    ],
)
def test_sparse_readiness_requires_valid_physical_indexes(collection, change):
    rows = [list(row) for row in sparse_catalog()]
    if change == "missing_term":
        rows.pop(0)
    elif change == "missing_primary":
        rows.pop(1)
    elif change == "invalid_term":
        rows[0][1] = False
    elif change == "unready_primary":
        rows[1][2] = False
    elif change == "wrong_method":
        rows[0][3] = "hash"
    elif change == "wrong_term_keys":
        rows[0][7:9] = ["id", "term"]
    elif change == "wrong_primary_keys":
        rows[1][7:9] = ["term", "id"]
    elif change == "partial":
        rows[0][9] = False
    elif change == "nonunique_primary":
        rows[1][5] = False
    collection._execute = Mock(return_value=rows)
    assert collection.validate_sparse_index() is (change == "valid")
    sql = collection._execute.call_args.args[0]
    assert "pg_catalog.pg_index" in sql and "i.indisvalid" in sql and "i.indisready" in sql


def test_metadata_capabilities_reject_invalid_sparse_index(collection):
    fresh = {"Fields": [], "_sparse_index": {"sparse_enabled": True, "sparse_ready": True}}
    invalid = sparse_catalog()
    invalid[0] = (*invalid[0][:1], False, *invalid[0][2:])
    collection._execute = Mock(side_effect=[[(json.dumps(fresh),)], invalid])
    collection.get_meta_data()
    from openviking_cli.exceptions import FailedPreconditionError

    with pytest.raises(FailedPreconditionError, match="physically ready"):
        collection._require_sparse_ready()


def make_adapter(collection):
    with patch.object(OpenGaussCollectionAdapter, "_connect"):
        adapter = OpenGaussCollectionAdapter("context", OpenGaussConfig())
    adapter._conn = collection._conn
    adapter._collection = Collection(collection)
    return adapter


@pytest.mark.parametrize("limit", [None, 1])
def test_filtered_delete_cleans_database_enabled_sparse_with_config_disabled(collection, limit):
    adapter = make_adapter(collection)
    collection.get_meta_data = Mock(return_value={"Fields": []})
    cursor = collection._conn.cursor.return_value
    cursor.fetchall.return_value = [("a",)]

    def execute(sql, params=None):
        if "information_schema.columns" in sql:
            cursor.fetchone.return_value = (True,)
        elif sql.startswith("SELECT meta_json"):
            cursor.fetchone.return_value = (
                json.dumps(
                    {
                        "_sparse_index": {
                            "sparse_enabled": True,
                            "sparse_ready": True,
                        }
                    }
                ),
            )

    cursor.execute.side_effect = execute
    assert adapter.delete(filter={"op": "must", "field": "id", "conds": ["a"]}, limit=limit) == 1
    assert any(
        call.args[0].startswith('DELETE FROM "_ov_sparse_context"')
        for call in sql_calls(collection)
    )
    collection._conn.commit.assert_called_once()


def test_adapter_drop_does_not_precommit_index_drops_and_retains_failed_handle(collection):
    adapter = make_adapter(collection)
    adapter.collection_exists = Mock(return_value=True)
    collection.drop_index = Mock()
    collection._conn.cursor.return_value.execute.side_effect = RuntimeError("drop")
    with pytest.raises(RuntimeError, match="drop"):
        adapter.drop_collection()
    assert adapter._collection is not None
    collection.drop_index.assert_not_called()
    collection._conn.commit.assert_not_called()


def test_filtered_delete_hook_failure_rolls_back(collection):
    adapter = make_adapter(collection)
    collection.get_meta_data = Mock(return_value={"Fields": []})
    cursor = collection._conn.cursor.return_value
    cursor.fetchall.return_value = [("a",)]
    cursor.fetchone.return_value = (True,)
    collection._delete_sparse_postings_on_cursor = Mock(side_effect=RuntimeError("postings"))
    with pytest.raises(RuntimeError, match="postings"):
        adapter.delete(filter={"op": "must", "field": "id", "conds": ["a"]})
    collection._conn.commit.assert_not_called()
    collection._conn.rollback.assert_called_once()
    cursor.close.assert_called_once()


@pytest.mark.parametrize("failure", ["missing_insert", "update", "commit"])
def test_collection_meta_save_failure_rolls_back(failure):
    conn = Mock()
    cursor = conn.cursor.return_value
    cursor.rowcount = 1
    cursor.fetchone.return_value = (
        None if failure == "missing_insert" else ('{"_sparse_index":{"candidate_limit":128}}',)
    )
    if failure == "commit":
        conn.commit.side_effect = RuntimeError("save")
    else:

        def execute(sql, params=None):
            if sql.startswith("INSERT" if failure == "missing_insert" else "UPDATE"):
                raise RuntimeError("save")

        cursor.execute.side_effect = execute
    with patch("openviking.storage.vectordb_adapters.opengauss_adapter._ensure_meta_table"):
        with pytest.raises(RuntimeError, match="save"):
            _save_collection_meta(conn, "context", {"_distance": "l2"})
    conn.rollback.assert_called_once()
    cursor.close.assert_called_once()


def test_ensure_vector_index_only_updates_index_metadata(collection):
    adapter = make_adapter(collection)
    collection._normalized_index_meta = Mock(
        return_value={"_pg_index_name": "idx_context_default_vec"}
    )
    collection._physical_index_definition = Mock(return_value="matching index")
    collection._index_definition_matches = Mock(return_value=True)
    collection._apply_parallel_workers = Mock()
    collection._persist_index_meta_and_scalar_indexes = Mock()
    state = {"sparse_enabled": True, "candidate_limit": 128, "sparse_ready": True}
    collection._meta["_sparse_index"] = deepcopy(state)
    adapter._ensure_vector_index_exists(collection)
    assert collection._meta["_sparse_index"] == state
    collection._conn.cursor.assert_not_called()
    collection._persist_index_meta_and_scalar_indexes.assert_called_once()


def test_collection_meta_save_preserves_database_retrieval_state():
    conn = Mock()
    cursor = conn.cursor.return_value
    cursor.rowcount = 1
    fresh = {"_sparse_index": {"candidate_limit": 128}, "Fields": [], "other": "fresh"}
    cursor.fetchone.return_value = (json.dumps(fresh),)
    with patch("openviking.storage.vectordb_adapters.opengauss_adapter._ensure_meta_table"):
        merged = _save_collection_meta(conn, "context", {"_distance": "l2"})
    assert merged == {**fresh, "_distance": "l2"}
    assert "FOR UPDATE" in cursor.execute.call_args_list[0].args[0]
    assert json.loads(cursor.execute.call_args.args[1][0]) == merged
    conn.commit.assert_called_once()


def test_reconnect_distance_update_only_patches_distance(collection):
    adapter = make_adapter(collection)
    adapter._collection = None
    adapter._distance_metric = "l2"
    adapter._table_exists = Mock(return_value=True)
    adapter._ensure_vector_index_exists = Mock()
    cursor = collection._conn.cursor.return_value
    fresh = {"_dim": 2, "_distance": "cosine", "Fields": [], "_sparse_index": {"candidate_limit": 128}}
    cursor.fetchone.return_value = (json.dumps(fresh),)
    with (
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter._load_collection_meta",
            return_value={
                "_dim": 2,
                "_distance": "cosine",
                "Fields": [],
            },
        ),
        patch("openviking.storage.vectordb_adapters.opengauss_adapter._ensure_meta_table"),
        patch(
            "openviking.storage.vectordb_adapters.opengauss_adapter.OpenGaussCollection",
            return_value=collection,
        ),
    ):
        adapter._load_existing_collection_if_needed()
    assert latest_metadata(collection) == {**fresh, "_distance": "l2"}
