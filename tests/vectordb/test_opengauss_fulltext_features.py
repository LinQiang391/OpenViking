# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Mock SQL tests: no server, extensions or language model calls."""

import json
import threading
from copy import deepcopy
from unittest.mock import Mock

import pytest

from openviking.storage.vectordb_adapters.opengauss.capabilities import (
    probe_fulltext_capabilities,
)
from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss.fulltext import OpenGaussFulltextMixin


class Harness(OpenGaussFulltextMixin):
    _normalize_filter_date_times = OpenGaussCollection._normalize_filter_date_times
    _row_to_dict = OpenGaussCollection._row_to_dict

    def __init__(self, state=None):
        self._lock = threading.RLock()
        self._conn = Mock()
        self._cursor = self._conn.cursor
        self.cursor = self._cursor.return_value
        self.cursor.rowcount = 1
        self._name = "context"
        self._meta = {"Fields": [], "_retrieval": deepcopy(state or {})}
        self._field_names = {"id", "uri", "updated_at"}
        self._date_time_fields = {"updated_at"}
        self._array_fields = {"tags"}
        self._distributed = False
        self._get_all_columns = Mock(return_value=["id", "uri", "updated_at", "content", "tags"])
        self._get_column_types = Mock(return_value={"content": "text", "updated_at": "bigint"})
        self._execute = Mock()
        self.cursor.fetchone.side_effect = lambda: (json.dumps(self._meta),)
        self.probe_fulltext_capabilities = Mock(return_value={"bm25": {"state": "available"}})


def ready():
    return {
        "engine": "bm25",
        "schema_ready": True,
        "state": "ready",
        "generation": 1,
        "index": {
            "name": "context_ov_bm25_g1",
            "engine": "bm25",
            "generation": 1,
        },
    }


def catalog(valid=True):
    return [(valid, True, "bm25", "content", True, 1)]


def statements(harness):
    return [call.args[0] for call in harness.cursor.execute.call_args_list]


def probe_connection(
    bm25=(True, True, True),
    *,
    settings=None,
    methods=None,
    sparse_types=None,
    sparse_operators=None,
    recovery=False,
    failures=None,
):
    conn = Mock()
    conn._local = None
    conn.autocommit = False
    conn.get_transaction_status.return_value = 0
    cursor = conn.cursor.return_value
    current = {"sql": ""}

    def execute(sql, params=None):
        current["sql"] = sql
        for marker, error in (failures or {}).items():
            if marker in sql:
                raise error

    def fetchone():
        sql = current["sql"]
        if sql == "SELECT version()":
            return ("openGauss mock",)
        if "amname = 'bm25'" in sql:
            return bm25
        if "pg_is_in_recovery" in sql:
            return (recovery,)
        if "current_user, session_user" in sql:
            return ("reader", "login")
        raise AssertionError(f"Unexpected fetchone query: {sql}")

    def fetchall():
        sql = current["sql"]
        if "pg_settings" in sql:
            return list((settings or {}).items())
        if "SELECT amname" in sql:
            return [(name,) for name in (methods or [])]
        if "SELECT t.oid" in sql:
            return sparse_types or []
        if "SELECT o.oprleft" in sql:
            return sparse_operators or []
        raise AssertionError(f"Unexpected fetchall query: {sql}")

    cursor.execute.side_effect = execute
    cursor.fetchone.side_effect = fetchone
    cursor.fetchall.side_effect = fetchall
    return conn


@pytest.mark.parametrize(
    "bm25,status",
    [
        ((True, True, True), "available"),
        ((False, False, False), "unsupported"),
        ((True, True, False), "permission_denied"),
    ],
)
def test_capability_probe_is_read_only_and_distinguishes_states(bm25, status):
    conn = probe_connection(bm25)
    result = probe_fulltext_capabilities(conn)
    assert result["bm25"]["state"] == status
    assert "fts" not in result
    sql = [call.args[0] for call in conn.cursor.return_value.execute.call_args_list]
    assert sql[0] == "SET TRANSACTION READ ONLY"
    assert all(
        statement.startswith(("SET TRANSACTION READ ONLY", "SELECT", "SAVEPOINT", "RELEASE"))
        for statement in sql
    )
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    conn.cursor.return_value.close.assert_called_once()


@pytest.mark.parametrize("code,status", [("42501", "permission_denied"), ("08006", "error")])
def test_probe_failure_never_becomes_unsupported(code, status):
    conn = probe_connection()
    error = RuntimeError("catalog inaccessible")
    error.pgcode = code
    conn.cursor.return_value.execute.side_effect = [None, error]
    result = probe_fulltext_capabilities(conn)
    assert result["bm25"]["state"] == status
    assert result["bm25"]["sqlstate"] == code
    conn.rollback.assert_called_once()


def test_probe_has_no_fts_capability_or_parser_queries():
    conn = probe_connection()
    result = probe_fulltext_capabilities(conn)
    assert "fts" not in result
    sql = " ".join(call.args[0] for call in conn.cursor.return_value.execute.call_args_list)
    assert all(
        marker not in sql for marker in ("to_tsvector", "tsquery", "ts_rank", "pg_ts_config")
    )


def test_probe_rejects_removed_text_search_config_before_any_sql():
    conn = probe_connection()
    with pytest.raises(TypeError, match="text_search_config"):
        probe_fulltext_capabilities(conn, text_search_config="simple")
    conn.cursor.assert_not_called()


def test_optional_bm25_catalog_failure_does_not_disable_other_capabilities():
    error = RuntimeError("bm25 catalog unavailable")
    error.pgcode = "42501"
    conn = probe_connection(failures={"amname = 'bm25'": error})
    cursor = conn.cursor.return_value
    result = probe_fulltext_capabilities(conn)
    assert result["bm25"]["state"] == "permission_denied"
    assert "fts" not in result
    assert result["index_methods"]["state"] == "available"
    assert result["server"]["identity"]["state"] == "available"
    assert any("ROLLBACK TO SAVEPOINT" in call.args[0] for call in cursor.execute.call_args_list)


def sql_error(code):
    error = RuntimeError("mock optional catalog failure")
    error.pgcode = code
    return error


def test_server_deployment_read_only_encoding_storage_and_index_methods():
    conn = probe_connection(
        settings={
            "server_encoding": "UTF8",
            "client_encoding": "UTF8",
            "default_transaction_read_only": "off",
            "transaction_read_only": "on",
            "enable_multiple_nodes": "on",
            "pgxc_node_type": "coordinator",
            "pgxc_node_name": "cn_1",
            "cluster_run_mode": "cluster_primary",
            "enable_default_ustore_table": "on",
            "default_table_orientation": "row",
        },
        methods=["bm25", "btree", "gin", "hnsw"],
    )
    result = probe_fulltext_capabilities(conn)
    server = result["server"]
    assert server["deployment"]["mode"] == "distributed"
    assert server["deployment"]["node_role"] == "coordinator"
    assert server["deployment"]["node_name"] == "cn_1"
    assert server["role"] == {"state": "available", "value": "primary"}
    assert server["identity"]["current_user"] == "reader"
    assert server["read_only"]["default_transaction_read_only"] is False
    assert server["read_only"]["transaction_read_only"] is True
    assert server["read_only"]["transaction_forced_read_only_by_probe"] is True
    assert server["encoding"]["server"] == "UTF8"
    assert result["storage"]["observed_defaults"]["enable_default_ustore_table"] == "on"
    assert result["storage"]["table_storage_verified"] is False
    assert result["index_methods"]["names"] == ["bm25", "btree", "gin", "hnsw"]


@pytest.mark.parametrize("recovery,role", [(True, "standby"), (False, "primary")])
def test_recovery_role_is_independent_of_forced_probe_read_only(recovery, role):
    conn = probe_connection(
        recovery=recovery,
        settings={
            "enable_multiple_nodes": "off",
            "default_transaction_read_only": "on",
            "transaction_read_only": "on",
        },
    )
    result = probe_fulltext_capabilities(conn)
    assert result["server"]["recovery"]["in_recovery"] is recovery
    assert result["server"]["role"]["value"] == role
    assert result["server"]["deployment"]["mode"] == "single"
    assert result["server"]["read_only"]["default_transaction_read_only"] is True


def test_null_recovery_and_conflicting_role_signals_remain_unknown():
    conn = probe_connection(
        recovery=None,
        settings={
            "enable_multiple_nodes": "off",
            "pgxc_node_type": "coordinator",
            "default_transaction_read_only": "unexpected-value",
        },
    )
    server = probe_fulltext_capabilities(conn)["server"]
    assert server["recovery"]["state"] == "unknown"
    assert server["role"]["value"] == "unknown"
    assert server["deployment"]["mode"] == "unknown"
    assert server["deployment"]["conflicting_signals"] is True
    assert server["read_only"]["state"] == "unknown"


def test_node_name_or_missing_settings_do_not_invent_deployment_or_storage():
    result = probe_fulltext_capabilities(probe_connection(settings={"pgxc_node_name": "dn_1"}))
    assert result["server"]["deployment"]["mode"] == "unknown"
    assert result["server"]["deployment"]["state"] == "unknown"
    assert result["server"]["read_only"]["default_transaction_read_only"] is None
    assert result["server"]["encoding"]["state"] == "unknown"
    assert result["storage"]["state"] == "unknown"
    assert result["storage"]["observed_defaults"] == {}


@pytest.mark.parametrize(
    "type_allowed,op_allowed,status",
    [
        (True, True, "available"),
        (False, True, "permission_denied"),
        (True, False, "permission_denied"),
    ],
)
def test_native_sparse_candidates_never_claim_implemented(type_allowed, op_allowed, status):
    conn = probe_connection(
        sparse_types=[(9001, "public", "sparsevec", True, type_allowed)],
        sparse_operators=[(9001, "public", "<#>", True, op_allowed)],
        methods=["hnsw"],
    )
    native = probe_fulltext_capabilities(conn)["sparse"]["native"]
    assert native["state"] == status
    assert native["implemented"] is False
    assert native["index_support_verified"] is False
    assert native["candidates"][0]["type"] == "sparsevec"
    assert native["candidates"][0]["operators"][0]["name"] == "<#>"


@pytest.mark.parametrize(
    "types,operators",
    [
        ([], []),
        ([(9001, "public", "sparsevec", True, True)], []),
        ([(9001, "hidden", "sparsevec", False, True)], [(9001, "hidden", "<#>", True, True)]),
        ([(9001, "public", "sparsevec", True, True)], [(9001, "hidden", "<#>", False, True)]),
        ([(9001, "public", "sparsevec", True, True)], [(9002, "public", "<#>", True, True)]),
    ],
)
def test_native_sparse_requires_visible_same_type_operator(types, operators):
    conn = probe_connection(sparse_types=types, sparse_operators=operators)
    native = probe_fulltext_capabilities(conn)["sparse"]["native"]
    assert native["state"] == "unsupported"
    assert native["implemented"] is False


@pytest.mark.parametrize(
    "code,state",
    [
        ("42P01", "unknown"),
        ("42703", "unknown"),
        ("42883", "unknown"),
        ("0A000", "unknown"),
        ("42501", "permission_denied"),
        ("XX000", "error"),
    ],
)
@pytest.mark.parametrize(
    "marker,path",
    [
        ("pg_catalog.pg_settings", ("server", "settings")),
        ("pg_catalog.pg_is_in_recovery", ("server", "recovery")),
        ("SELECT amname", ("index_methods",)),
        ("SELECT current_user, session_user", ("server", "identity")),
        ("SELECT t.oid", ("sparse", "native")),
        ("SELECT o.oprleft", ("sparse", "native")),
    ],
)
def test_extra_catalog_errors_are_isolated_and_missing_catalog_is_unknown(
    code, state, marker, path
):
    conn = probe_connection(failures={marker: sql_error(code)})
    result = probe_fulltext_capabilities(conn)
    assert result["bm25"]["state"] == "available"
    assert "fts" not in result
    component = result
    for key in path:
        component = component[key]
    assert component["state"] == state and component["sqlstate"] == code
    sql = [call.args[0] for call in conn.cursor.return_value.execute.call_args_list]
    assert any(statement.startswith("ROLLBACK TO SAVEPOINT ov_cap_") for statement in sql)
    assert not any("CREATE" in statement or "ALTER" in statement for statement in sql)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    assert result["sparse"]["native"]["implemented"] is False


def test_missing_original_engine_catalog_also_means_unknown_not_unsupported():
    conn = probe_connection(failures={"amname = 'bm25'": sql_error("42P01")})
    result = probe_fulltext_capabilities(conn)
    assert result["bm25"]["state"] == "unknown"
    assert "fts" not in result


def test_probe_rejects_active_transaction_without_touching_it():
    conn = probe_connection()
    conn.get_transaction_status.return_value = 2
    with pytest.raises(RuntimeError, match="idle"):
        probe_fulltext_capabilities(conn)
    conn.cursor.assert_not_called()
    conn.rollback.assert_not_called()


def test_probe_rejects_autocommit():
    conn = probe_connection()
    conn.autocommit = True
    with pytest.raises(ValueError, match="autocommit"):
        probe_fulltext_capabilities(conn)
    conn.cursor.assert_not_called()


def test_configure_defaults_to_bm25_without_ddl_and_never_falls_back():
    coll = Harness()
    state = coll.configure_fulltext()
    assert state["engine"] == "bm25" and state["state"] == "configured"
    assert "text_search_config" not in state
    coll.probe_fulltext_capabilities.assert_called_once_with()
    assert not any("CREATE" in sql or "ALTER" in sql for sql in statements(coll))
    coll.configure_fulltext("disabled")
    coll.probe_fulltext_capabilities.return_value["bm25"]["state"] = "unsupported"
    before = deepcopy(coll._meta)
    with pytest.raises(RuntimeError, match="unsupported"):
        coll.configure_fulltext("bm25")
    assert coll._meta == before
    assert coll._meta["_retrieval"]["engine"] == "disabled"


@pytest.mark.parametrize("engine", ["fts", "auto", "gin", "bm25'); DROP TABLE x; --"])
def test_configure_rejects_removed_or_untrusted_engine_before_any_sql(engine):
    coll = Harness()
    with pytest.raises(ValueError, match="engine|bm25"):
        coll.configure_fulltext(engine)
    coll.cursor.execute.assert_not_called()
    coll.probe_fulltext_capabilities.assert_not_called()


@pytest.mark.parametrize(
    "method",
    [
        "migrate_content_schema",
        "build_fulltext_index",
        "rebuild_fulltext_index",
        "drop_fulltext_index",
    ],
)
def test_ddl_is_disabled_by_default(method):
    coll = Harness()
    with pytest.raises(PermissionError, match="allow_ddl"):
        getattr(coll, method)()
    coll._execute.assert_not_called()
    coll.cursor.execute.assert_not_called()


def test_migrate_content_and_state_share_one_transaction():
    coll = Harness()
    coll._get_column_types.return_value = {}
    state = coll.migrate_content_schema(allow_ddl=True)
    assert state["schema_ready"]
    assert statements(coll)[0] == 'LOCK TABLE "context" IN SHARE MODE'
    assert statements(coll)[1] == 'ALTER TABLE "context" ADD COLUMN "content" TEXT'
    assert statements(coll)[2] == (
        'ALTER TABLE "context" ADD COLUMN "_ov_content_empty" BOOLEAN NOT NULL DEFAULT FALSE'
    )
    assert "FOR UPDATE" in statements(coll)[3]
    assert "UPDATE" in statements(coll)[4]
    coll._conn.commit.assert_called_once()
    assert "content" in coll._field_names


def test_migration_does_not_accept_incompatible_column_or_publish_failed_state():
    coll = Harness()
    coll._get_column_types.return_value = {"content": "integer"}
    with pytest.raises(ValueError, match="TEXT"):
        coll.migrate_content_schema(allow_ddl=True)
    coll._get_column_types.return_value = {}
    coll._persist_retrieval_state = Mock(side_effect=RuntimeError("metadata failed"))
    with pytest.raises(RuntimeError, match="metadata failed"):
        coll.migrate_content_schema(allow_ddl=True)
    assert coll._meta["_retrieval"] == {}
    assert "content" not in coll._field_names
    coll._conn.rollback.assert_called_once()


def test_persistence_rejects_missing_and_stale_metadata():
    coll = Harness()
    coll.cursor.fetchone.side_effect = None
    coll.cursor.fetchone.return_value = None
    with pytest.raises(RuntimeError, match="missing"):
        coll.configure_fulltext("disabled")
    coll.cursor.fetchone.return_value = (json.dumps({"_retrieval": {"generation": 20}}),)
    with pytest.raises(RuntimeError, match="Stale"):
        coll.configure_fulltext("disabled")
    assert coll._meta["_retrieval"] == {}


def test_persistence_preserves_other_metadata_fields():
    coll = Harness()
    coll.cursor.fetchone.side_effect = None
    coll.cursor.fetchone.return_value = (json.dumps({"Description": "fresh", "_retrieval": {}}),)
    coll.configure_fulltext("disabled")
    assert coll._meta["Description"] == "fresh"


def prepare_backfill(coll, *, after=None, rows=None, missing=False):
    coll._meta["_retrieval"].update({"schema_ready": True})
    if after:
        coll._meta["_retrieval"]["checkpoint"] = {"after_id": after, "upper_id": "z"}
        coll._execute.return_value = rows or []
    else:
        coll._execute.side_effect = [[("z",)], rows or []]

    def fetchone():
        if statements(coll)[-1].startswith("SELECT EXISTS"):
            return (missing,)
        return (json.dumps(coll._meta),)

    coll.cursor.fetchone.side_effect = fetchone


def test_backfill_keyset_resume_update_only_and_atomic_checkpoint():
    coll = Harness()
    prepare_backfill(coll, after="a", rows=[("b", 123, "viking://b", "oldhash")], missing=True)
    loader = Mock(return_value="stored raw text")
    result = coll.backfill_content_batch(loader, batch_size=1)
    sql, params = coll._execute.call_args.args
    assert '"id" COLLATE "C" > %s' in sql
    assert "OFFSET" not in sql and params == ["z", "a", 1]
    assert result == {"updated": 1, "done": False, "checkpoint": {"after_id": "b", "upper_id": "z"}}
    update = next(
        call
        for call in coll.cursor.execute.call_args_list
        if call.args[0].startswith('UPDATE "context"')
    )
    assert '"updated_at" IS NOT DISTINCT FROM %s' in update.args[0]
    assert "md5" in update.args[0] and '"content" IS NULL' in update.args[0]
    assert list(update.args[1]) == ["stored raw text", "b", 123, "oldhash"]
    assert not any("INSERT" in sql for sql in statements(coll))
    loader.assert_called_once_with({"id": "b", "updated_at": 123, "uri": "viking://b"})
    coll._conn.commit.assert_called_once()


def test_backfill_conflict_or_deleted_row_does_not_advance_checkpoint():
    coll = Harness()
    prepare_backfill(coll, after="a", rows=[("b", 123, "uri", "hash")])
    before = deepcopy(coll._meta)
    coll.cursor.rowcount = 0
    with pytest.raises(RuntimeError, match="snapshot changed"):
        coll.backfill_content_batch(lambda snapshot: "new")
    assert coll._meta == before
    coll._conn.commit.assert_not_called()
    coll._conn.rollback.assert_called_once()


def test_backfill_loader_failure_does_not_write_or_advance():
    coll = Harness()
    prepare_backfill(coll, after="a", rows=[("b", 123, "uri", "hash")])
    before = deepcopy(coll._meta)
    with pytest.raises(ValueError, match="loader"):
        coll.backfill_content_batch(lambda snapshot: None)
    coll.cursor.execute.assert_not_called()
    assert coll._meta == before


def test_backfill_exhausted_pass_restarts_if_earlier_ids_are_missing():
    coll = Harness()
    prepare_backfill(coll, after="m", rows=[], missing=True)
    result = coll.backfill_content_batch(lambda snapshot: "unused")
    assert not result["done"]
    assert result["checkpoint"] == {"after_id": None, "upper_id": None}


def test_idempotent_backfill_does_not_disable_ready_index():
    coll = Harness(ready())
    prepare_backfill(coll, after="a", rows=[], missing=False)
    assert coll.backfill_content_batch(lambda snapshot: "unused")["done"]
    assert coll._meta["_retrieval"]["state"] == "ready"


def test_backfill_empty_table_can_complete_without_loader():
    coll = Harness()
    prepare_backfill(coll, after="a", rows=[], missing=False)
    loader = Mock()
    assert coll.backfill_content_batch(loader)["done"]
    loader.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        (False, True, "bm25", "content", True, 1),
        (True, False, "bm25", "content", True, 1),
        (True, True, "gin", "content", True, 1),
        (True, True, "bm25", "uri", True, 1),
        (True, True, "bm25", "content", False, 1),
        (True, True, "bm25", "content", True, 2),
    ],
)
def test_ready_requires_valid_matching_physical_index(change):
    coll = Harness(ready())
    coll._execute.return_value = [change]
    assert not coll.validate_fulltext_index()


@pytest.mark.parametrize("engine", ["fts", "disabled", "auto", ""])
@pytest.mark.parametrize(
    "method",
    [
        "validate_fulltext_index",
        "build_fulltext_index",
        "rebuild_fulltext_index",
        "search_by_keywords",
    ],
)
def test_management_and_search_reject_non_bm25_before_any_sql(method, engine):
    coll = Harness(ready())
    options = {"engine": engine}
    args = ()
    if method in {"build_fulltext_index", "rebuild_fulltext_index"}:
        options["allow_ddl"] = True
    if method == "search_by_keywords":
        args = ("default",)
        options["query"] = "retrieval"
    with pytest.raises(ValueError, match="engine|bm25"):
        getattr(coll, method)(*args, **options)
    coll._execute.assert_not_called()
    coll.cursor.execute.assert_not_called()
    coll.probe_fulltext_capabilities.assert_not_called()


def test_fulltext_metadata_alone_is_never_ready():
    coll = Harness()
    coll._meta["FullText"] = {"Enabled": True}
    assert not coll.validate_fulltext_index()
    coll._execute.assert_not_called()
    coll = Harness(ready())
    coll._execute.return_value = []
    assert not coll.validate_fulltext_index()


def test_native_build_and_rebuild_are_transactional_and_verified():
    state = ready()
    state["state"] = "backfilled"
    state.pop("index")
    state["generation"] = 0
    coll = Harness(state)

    def fetchone():
        return (
            (False,)
            if statements(coll)[-1].startswith("SELECT EXISTS")
            else (json.dumps(coll._meta),)
        )

    coll.cursor.fetchone.side_effect = fetchone
    coll.cursor.fetchall.return_value = catalog()
    result = coll.build_fulltext_index(engine="bm25", allow_ddl=True)
    assert result["state"] == "ready" and result["generation"] == 1
    assert "text_search_config" not in result and "text_search_config" not in result["index"]
    assert any("USING bm25" in sql for sql in statements(coll))
    assert any("pg_index" in sql for sql in statements(coll))
    assert not any("CREATE EXTENSION" in sql for sql in statements(coll))
    coll.rebuild_fulltext_index(allow_ddl=True)
    assert coll._meta["_retrieval"]["generation"] == 2
    sql = statements(coll)
    assert next(i for i, statement in enumerate(sql) if "_g2" in statement) < next(
        i for i, statement in enumerate(sql) if "DROP INDEX" in statement
    )


def test_build_refuses_missing_content_and_rolls_back_state():
    coll = Harness(ready())
    coll.cursor.fetchone.side_effect = None
    coll.cursor.fetchone.return_value = (True,)
    coll.cursor.fetchall.return_value = catalog()
    before = deepcopy(coll._meta)
    with pytest.raises(RuntimeError, match="backfill"):
        coll.rebuild_fulltext_index(allow_ddl=True)
    assert coll._meta == before
    assert not any("CREATE INDEX" in sql for sql in statements(coll))
    coll._conn.rollback.assert_called_once()


@pytest.mark.parametrize("old_rows", [[], catalog(valid=False)])
def test_rebuild_recovers_missing_or_invalid_owned_index(old_rows):
    coll = Harness(ready())

    def fetchone():
        return (
            (False,)
            if statements(coll)[-1].startswith("SELECT EXISTS")
            else (json.dumps(coll._meta),)
        )

    coll.cursor.fetchone.side_effect = fetchone

    def fetchall():
        sql, params = coll.cursor.execute.call_args.args
        assert "pg_index" in sql
        assert params[0] == "context"
        if params[1] == "context_ov_bm25_g1":
            return old_rows
        assert params[1] == "context_ov_bm25_g2"
        return catalog()

    coll.cursor.fetchall.side_effect = fetchall
    result = coll.rebuild_fulltext_index(allow_ddl=True)
    assert result["generation"] == 2 and result["state"] == "ready"
    sql = statements(coll)
    assert any("DROP INDEX" in statement for statement in sql) == bool(old_rows)
    if old_rows:
        assert next(i for i, statement in enumerate(sql) if "CREATE INDEX" in statement) < next(
            i for i, statement in enumerate(sql) if "DROP INDEX" in statement
        )
    coll._conn.commit.assert_called_once()
    coll._conn.rollback.assert_not_called()


@pytest.mark.parametrize("method", ["rebuild_fulltext_index", "drop_fulltext_index"])
@pytest.mark.parametrize(
    "physical",
    [
        (True, True, "gin", "content", True, 1),
        (True, True, "bm25", "uri", True, 1),
    ],
)
def test_wrong_owned_index_requires_manual_cleanup_without_ddl(method, physical):
    coll = Harness(ready())
    before = deepcopy(coll._meta)
    coll.cursor.fetchall.return_value = [physical]
    with pytest.raises(RuntimeError, match="manual cleanup"):
        getattr(coll, method)(allow_ddl=True)
    assert coll._meta == before
    assert not any("CREATE INDEX" in sql or "DROP INDEX" in sql for sql in statements(coll))
    coll._conn.commit.assert_not_called()
    coll._conn.rollback.assert_called_once()


def test_failed_rebuild_preserves_old_ready_generation():
    coll = Harness(ready())
    before = deepcopy(coll._meta)
    coll.cursor.fetchone.side_effect = None
    coll.cursor.fetchone.return_value = (False,)

    def fetchall():
        _, params = coll.cursor.execute.call_args.args
        return catalog(valid=params[1] == "context_ov_bm25_g1")

    coll.cursor.fetchall.side_effect = fetchall
    with pytest.raises(RuntimeError, match="physical catalog"):
        coll.rebuild_fulltext_index(allow_ddl=True)
    assert coll._meta == before
    assert not any("DROP INDEX" in sql for sql in statements(coll))
    coll._conn.rollback.assert_called_once()


def test_failed_validation_never_marks_ready():
    state = ready()
    state["state"] = "backfilled"
    state.pop("index")
    coll = Harness(state)
    coll.cursor.fetchone.side_effect = None
    coll.cursor.fetchone.return_value = (False,)
    coll.cursor.fetchall.return_value = catalog(valid=False)
    with pytest.raises(RuntimeError, match="physical catalog"):
        coll.build_fulltext_index(allow_ddl=True)
    assert coll._meta["_retrieval"]["state"] == "backfilled"
    coll._conn.rollback.assert_called_once()


def test_drop_checks_identity_and_updates_state():
    coll = Harness(ready())
    coll.cursor.fetchall.return_value = catalog()
    result = coll.drop_fulltext_index(allow_ddl=True)
    assert result["state"] == "configured" and "index" not in result
    assert any("DROP INDEX" in sql for sql in statements(coll))


def test_disabled_collection_never_silently_satisfies_bm25_keywords():
    coll = Harness({"engine": "disabled", "state": "disabled"})
    with pytest.raises(RuntimeError, match="ready bm25"):
        coll.search_by_keywords("default", query="term")
    coll._execute.assert_not_called()
    coll.cursor.execute.assert_not_called()


def test_keyword_filters_dates_projection_and_score_conversion():
    coll = Harness(ready())
    coll._execute.return_value = catalog()
    coll.cursor.description = [(name,) for name in ["id", "uri", "content", "_ov_fulltext_score"]]
    row = ("doc", "viking://doc", "actual content", "1.25")
    coll.cursor.fetchall.return_value = [("a", *row[1:]), ("b", *row[1:]), row]
    query = "' ; DROP TABLE context --"
    result = coll.search_by_keywords(
        "default",
        query=query,
        engine="bm25",
        output_fields=["uri", "content"],
        filters={
            "op": "and",
            "conds": [
                {"op": "range", "field": "updated_at", "gte": "1970-01-01T00:00:01Z"},
                {"op": "must", "field": "tags", "conds": ["tenant"]},
            ],
        },
        limit=7,
        offset=2,
    )
    sql, params = coll.cursor.execute.call_args.args
    assert query not in sql and query in params
    assert 1000 in params and "tenant" in params
    assert '"updated_at" >= %s' in sql and 'ANY("tags")' in sql
    assert "NULL AS" not in sql
    assert params[-2:] == [query, 10]
    assert "<&>" in sql
    assert "ts_rank" not in sql and "tsquery" not in sql
    assert result.data[0].id == "doc"
    assert result.data[0].fields == {"uri": "viking://doc", "content": "actual content"}
    assert result.data[0].score == 1.25


def test_query_failure_rolls_back_and_invalid_fields_are_rejected():
    coll = Harness(ready())
    coll._execute.return_value = catalog()
    with pytest.raises(ValueError, match="Unknown"):
        coll.search_by_keywords("default", query="term", output_fields=["not_a_column"])
    coll.cursor.execute.side_effect = RuntimeError("query failed")
    with pytest.raises(RuntimeError, match="query failed"):
        coll.search_by_keywords("default", query="term")
    coll._conn.rollback.assert_called_once()
    coll.cursor.close.assert_called_once()


def test_configure_rejects_removed_text_search_config_before_any_sql():
    coll = Harness()
    with pytest.raises(TypeError, match="text_search_config"):
        coll.configure_fulltext("bm25", text_search_config="simple")
    coll.cursor.execute.assert_not_called()
    coll.probe_fulltext_capabilities.assert_not_called()


def test_collection_probe_accepts_no_configuration(monkeypatch):
    coll = Harness()
    probe = Mock(return_value={"bm25": {"state": "available"}})
    monkeypatch.setattr(
        "openviking.storage.vectordb_adapters.opengauss.fulltext.probe_fulltext_capabilities",
        probe,
    )
    assert OpenGaussFulltextMixin.probe_fulltext_capabilities(coll) == probe.return_value
    probe.assert_called_once_with(coll._conn)
    with pytest.raises(TypeError, match="text_search_config"):
        OpenGaussFulltextMixin.probe_fulltext_capabilities(coll, text_search_config="simple")
    assert probe.call_count == 1
