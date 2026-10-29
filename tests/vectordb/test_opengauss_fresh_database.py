"""Fresh-schema acceptance through the standard adapter API, dedicated port 35436 only."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock
from urllib.parse import unquote

import pytest
from test_opengauss_acceptance_helper import acceptance_isolation, acceptance_target  # noqa: F401

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig

pytestmark = pytest.mark.integration


def sql(adapter, statement, params=None):
    cursor = adapter._conn.cursor()
    try:
        cursor.execute(statement, params)
        rows = cursor.fetchall() if cursor.description else []
        adapter._conn.commit()
        return rows
    except Exception:
        adapter._conn.rollback()
        raise
    finally:
        cursor.close()


def physical_indexes(adapter):
    return sql(adapter, """
        SELECT idx.relname, am.amname, i.indisvalid, i.indisready,
               pg_get_indexdef(i.indexrelid)
        FROM pg_index i JOIN pg_class tbl ON tbl.oid=i.indrelid
        JOIN pg_namespace ns ON ns.oid=tbl.relnamespace
        JOIN pg_class idx ON idx.oid=i.indexrelid
        JOIN pg_am am ON am.oid=idx.relam
        WHERE ns.nspname=current_schema() AND tbl.relname=%s ORDER BY idx.relname
    """, (adapter.collection_name,))


@pytest.fixture
def fresh_factory(acceptance_isolation):  # noqa: F811
    target = acceptance_target()
    config = OpenGaussConfig(
        host=target.hostname, port=target.port, user=unquote(target.username),
        password=unquote(target.password or ""), db_name="postgres",
    )
    adapters = []

    def make(name="contextCollection", *, create=True, schema=None, index_type=None):
        adapter_config = config if index_type is None else config.model_copy(update={"index_type": index_type})
        adapter = OpenGaussCollectionAdapter(name, adapter_config)
        adapters.append(adapter)
        if create:
            assert adapter.create_collection(
                name, schema or CollectionSchemas.context_collection(name, 8),
                distance="cosine", sparse_weight=0.0, index_name="default",
            )
        return adapter

    try:
        yield make
    finally:
        # The isolation fixture owns the schema, including failed-create remnants.
        for adapter in adapters:
            adapter.close()


@pytest.fixture
def fresh(fresh_factory):
    return fresh_factory()


def test_standard_schema_creates_text_and_physical_bm25_on_empty_collection(fresh):
    assert fresh.USE_CONTENT_FIELD is True
    assert sql(fresh, """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name=%s AND column_name='content'
    """, (fresh.collection_name,)) == [("text",)]
    indexes = [row for row in physical_indexes(fresh) if row[1] == "bm25"]
    assert len(indexes) == 1
    assert indexes[0][2:4] == (True, True)
    assert "content" in indexes[0][4]
    assert fresh.search_by_keywords(query="retrieval", mode="bm25") == []
    assert fresh.query(query_vector=[1.0] + [0.0] * 7) == []
    assert fresh.get([]) == []
    assert fresh.upsert([]) == []


def test_body_keywords_chinese_filter_update_and_delete(fresh):
    records = [
        {"id": "en", "account_id": "a", "uri": "viking://resources/en",
         "content": "OpenViking retrieval transaction", "abstract": "summaryonlytoken"},
        {"id": "zh", "account_id": "a", "uri": "viking://resources/zh",
         "content": "数据库检索支持向量搜索。"},
        {"id": "private", "account_id": "b", "content": "OpenViking 数据库"},
    ]
    before = deepcopy(records)
    fresh.upsert(records)
    assert records == before
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    for query, expected in [("OpenViking", {"en"}), ("数据库", {"zh"}),
                            ("summaryonlytoken", set()), ("absentlexeme", set())]:
        hits = fresh.search_by_keywords(query=query, mode="bm25", filter=scope, fields=["content"])
        assert {hit["id"] for hit in hits} == expected
        assert all(hit["_score"] > 0 for hit in hits)
    fresh.update_data([{"id": "en", "content": "replacementtoken 正文更新"}])
    assert fresh.search_by_keywords(query="retrieval", mode="bm25", filter=scope) == []
    assert [hit["id"] for hit in fresh.search_by_keywords(query="replacementtoken", filter=scope)] == ["en"]
    assert fresh.delete(ids=["en"]) == 1
    assert fresh.get(["en"]) == []
    assert fresh.search_by_keywords(query="replacementtoken", filter=scope) == []


@pytest.mark.parametrize("method", ["upsert", "update_data"])
@pytest.mark.parametrize("content", ["", "中文正文 retrieval", None])
def test_partial_and_null_content_follow_normal_crud_semantics(fresh, method, content):
    fresh.upsert([{"id": "row", "content": content, "account_id": "a"}])
    patch = [{"id": "row", "account_id": "changed"}]
    original = deepcopy(patch)
    getattr(fresh, method)(patch)
    assert patch == original
    row = fresh.get(["row"])[0]
    assert row["content"] == content
    assert row["account_id"] == "changed"
    assert "_ov_content_empty" not in row
    getattr(fresh, method)([{"id": "row", "content": None}])
    assert fresh.get(["row"])[0]["content"] is None
    getattr(fresh, method)([{"id": "row", "content": "restoredtoken"}])
    assert [hit["id"] for hit in fresh.search_by_keywords(query="restoredtoken")] == ["row"]


def test_default_and_explicit_empty_roundtrip_in_a_mode(fresh):
    assert sql(fresh, "SHOW sql_compatibility")[0][0] == "A"
    fresh.upsert([{"id": "default"}, {"id": "empty", "content": ""}])
    for row in fresh.get(["default", "empty"]):
        assert row["content"] == ""
        assert "_ov_content_empty" not in row
    hits = fresh.query(filter={"op": "must", "field": "id", "conds": ["empty"]}, output_fields=["content"])
    assert len(hits) == 1 and hits[0]["content"] == ""
    assert fresh.search_by_keywords(query="absentlexeme") == []


def test_close_reopen_does_not_issue_ddl(fresh, fresh_factory, monkeypatch):
    fresh.upsert([{"id": "saved", "content": "persistedtoken"}])
    before = physical_indexes(fresh)
    fresh.close()
    from openviking.storage.vectordb_adapters.opengauss.connection import _PooledConnectionProxy

    original = _PooledConnectionProxy.cursor
    statements = []

    class ReadCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def execute(self, statement, params=None):
            statements.append(statement)
            assert not statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ", "REINDEX "))
            return self.cursor.execute(statement, params)

    monkeypatch.setattr(_PooledConnectionProxy, "cursor", lambda self, *a, **kw: ReadCursor(original(self, *a, **kw)))
    reopened = fresh_factory(create=False)
    assert reopened.USE_CONTENT_FIELD is True
    assert reopened.get(["saved"])[0]["content"] == "persistedtoken"
    assert [hit["id"] for hit in reopened.search_by_keywords(query="persistedtoken")] == ["saved"]
    assert physical_indexes(reopened) == before
    assert statements


@pytest.mark.parametrize("damage", ["drop", "wrong_method", "wrong_column"])
def test_physical_damage_is_not_ready_and_queries_never_repair(fresh, fresh_factory, damage):
    fresh.upsert([{"id": "row", "content": "retrieval"}])
    index = next(row[0] for row in physical_indexes(fresh) if row[1] == "bm25")
    assert index.replace("_", "").isalnum()
    sql(fresh, f'DROP INDEX "{index}"')
    if damage != "drop":
        definition = "btree(content)" if damage == "wrong_method" else "bm25(abstract)"
        sql(fresh, f'CREATE INDEX "{index}" ON "contextCollection" USING {definition}')
    damaged = physical_indexes(fresh)
    fresh.close()
    reopened = fresh_factory(create=False)
    assert reopened.USE_CONTENT_FIELD is True
    assert reopened.get_collection().get_meta_data().get("FullText", []) == []
    with pytest.raises(FailedPreconditionError, match="ready"):
        reopened.search_by_keywords(query="retrieval", mode="bm25")
    assert physical_indexes(reopened) == damaged


def test_old_metadata_without_content_warns_without_claiming_capability(fresh_factory, monkeypatch):
    schema = CollectionSchemas.context_collection("legacy", 8)
    schema["Fields"] = [field for field in schema["Fields"] if field["FieldName"] != "content"]
    schema.pop("FullText")
    legacy = fresh_factory("legacy", schema=schema)
    legacy.close()
    import openviking.storage.vectordb_adapters.opengauss_adapter as module

    warning = Mock()
    monkeypatch.setattr(module.logger, "warning", warning)
    reopened = fresh_factory("legacy", create=False)
    metadata = reopened.get_collection().get_meta_data()
    assert "content" not in {field["FieldName"] for field in metadata["Fields"]}
    assert metadata.get("FullText", []) == []
    assert warning.called
    assert "content" in " ".join(str(call) for call in warning.call_args_list).lower()
    assert not [row for row in physical_indexes(reopened) if row[1] == "bm25"]


def test_failed_create_cleans_only_its_owned_objects(fresh_factory, monkeypatch):
    kept = fresh_factory("kept")
    kept.upsert([{"id": "keep", "content": "mustsurvive"}])
    failed = fresh_factory("failed", create=False)
    from openviking.storage.vectordb.collection.collection import Collection

    def fail_index(*args, **kwargs):
        raise RuntimeError("injected index creation failure")

    with monkeypatch.context() as patch:
        patch.setattr(Collection, "create_index", fail_index)
        with pytest.raises(RuntimeError, match="injected index"):
            failed.create_collection("failed", CollectionSchemas.context_collection("failed", 8),
                                     distance="cosine", sparse_weight=0.0, index_name="default")
    assert sql(failed, "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s", ("failed",)) == []
    assert sql(failed, 'SELECT table_name FROM "_ov_collection_meta" WHERE table_name=%s', ("failed",)) == []
    assert kept.get(["keep"])[0]["content"] == "mustsurvive"
    assert [hit["id"] for hit in kept.search_by_keywords(query="mustsurvive")] == ["keep"]


def test_committed_data_survives_ann_build_failure_and_explicit_retry(fresh_factory, monkeypatch):
    from openviking_cli.exceptions import FailedPreconditionError

    adapter = fresh_factory("ann_retry", index_type="ivfflat")
    collection = adapter.get_collection()._Collection__collection
    assert "default" in collection._pending_indexes
    with monkeypatch.context() as patch:
        patch.setattr(collection, "_materialize_index", Mock(side_effect=RuntimeError("injected build failure")))
        adapter.upsert([{"id": "saved", "content": "survivingkeyword", "vector": [1.0] + [0.0] * 7}])
    assert adapter.get(["saved"])[0]["content"] == "survivingkeyword"
    assert [hit["id"] for hit in adapter.search_by_keywords(query="survivingkeyword")] == ["saved"]
    with pytest.raises(FailedPreconditionError):
        adapter.query(query_vector=[1.0] + [0.0] * 7)
    metadata = collection.get_index_meta_data("default")
    collection.create_index("default", metadata)
    assert [hit["id"] for hit in adapter.query(query_vector=[1.0] + [0.0] * 7)] == ["saved"]


@pytest.mark.asyncio
@pytest.mark.parametrize("node_limit", [None, 2], ids=["unlimited", "bounded"])
@pytest.mark.parametrize("cached_metadata", [True, False], ids=["query-error", "direct-fs"])
async def test_tagged_grep_enumerates_all_pages_without_ann(fresh, monkeypatch, node_limit, cached_metadata):
    from tests.storage.test_grep_fulltext_backend_compat import (
        ROOT,
        TAG_FILTER,
        _backend,
        _ctx,
        _facade,
        _fs,
    )

    collection = fresh.get_collection()._Collection__collection
    sql(fresh, f'''INSERT INTO "{fresh.collection_name}"
        (id, uri, account_id, search_tags)
        SELECT 'tag-' || lpad(i::text, 6, '0'), '/resources/tag-' || i::text || '.md',
               'account', ARRAY['env=prod']::text[]
        FROM generate_series(0, 100000) AS i''')
    ann_meta = collection.get_index_meta_data("default")
    sql(fresh, f'DROP INDEX "{ann_meta["_pg_index_name"]}"')
    assert not collection.has_index("default")
    assert collection.validate_fulltext_index("bm25")
    ready_metadata = fresh.get_collection().get_meta_data()
    index = next(row[0] for row in physical_indexes(fresh) if row[1] == "bm25")
    sql(fresh, f'DROP INDEX "{index}"')
    assert not collection.validate_fulltext_index("bm25")

    backend = _backend("opengauss")
    backend.get_collection_meta = (
        AsyncMock(return_value=ready_metadata) if cached_metadata
        else AsyncMock(side_effect=fresh.get_collection().get_meta_data)
    )
    page_sizes = []
    enumerated_ids = []

    async def query_page(**kwargs):
        records = fresh.query(**kwargs)
        page_sizes.append(len(records))
        enumerated_ids.extend(record["id"] for record in records)
        return records

    backend.query = AsyncMock(side_effect=query_page)
    backend.search_by_keywords = AsyncMock(side_effect=fresh.search_by_keywords)
    store = _facade({"account": backend})
    fs = _fs(store)
    extra = ROOT + "/tag-100000.md"
    captured = []

    async def scan(**kwargs):
        captured.append(kwargs["allowed_uris"])
        return {"matches": [{"uri": extra, "line": 1, "content": "needle"}],
                "count": 1, "match_count": 1, "files_scanned": 1}

    fs._grep_fs = scan
    monkeypatch.setattr(collection, "search_by_vector", Mock(side_effect=AssertionError("ANN must not run")))
    result = await fs.grep(ROOT, "needle", ctx=_ctx(), tag_filter=TAG_FILTER,
                           include_tags=True, node_limit=node_limit)
    assert len(captured) == 1
    assert captured[0] == {ROOT + f"/tag-{i}.md" for i in range(100001)}
    assert result["matches"][0]["tags"] == ["env=prod"]
    assert [entry.kwargs["offset"] for entry in backend.query.await_args_list] == [0, 100000]
    assert all(entry.kwargs["order_by"] == "id" and entry.kwargs["order_desc"] is False
               for entry in backend.query.await_args_list)
    assert page_sizes == [100000, 1]
    assert enumerated_ids == [f"tag-{i:06d}" for i in range(100001)]
    assert backend.search_by_keywords.await_count == int(cached_metadata)
    collection.search_by_vector.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("node_limit", [None, 2], ids=["unlimited", "bounded"])
@pytest.mark.parametrize("has_candidate", [False, True], ids=["empty", "partial"])
async def test_grep_success_returns_only_bm25_candidates(fresh, node_limit, has_candidate):
    from tests.storage.test_grep_fulltext_backend_compat import (
        FILE,
        ROOT,
        _backend,
        _ctx,
        _facade,
        _fs,
    )

    fresh.upsert([{"id": "indexed", "uri": FILE, "account_id": "account",
                   "content": "needle" if has_candidate else "unrelated"}])
    backend = _backend("opengauss")
    backend.get_collection_meta = AsyncMock(side_effect=fresh.get_collection().get_meta_data)
    backend.search_by_keywords = AsyncMock(side_effect=fresh.search_by_keywords)
    fs = _fs(_facade({"account": backend}))
    fs.read = AsyncMock(return_value=b"needle\nnot a match")
    fs._grep_fs = AsyncMock(side_effect=AssertionError("successful recall must not scan FS"))
    result = await fs.grep(ROOT, "needle", ctx=_ctx(), node_limit=node_limit)
    assert result["count"] == result["match_count"] == int(has_candidate)
    assert result["files_scanned"] == int(has_candidate)
    assert backend.search_by_keywords.await_args.kwargs["limit"] == (100000 if node_limit is None else 10)
    fs._grep_fs.assert_not_awaited()
    backend.query.assert_not_awaited()


def test_orphan_create_refusal_never_deletes_unowned_table(fresh_factory):
    adapter = fresh_factory("orphan", create=False)
    sql(adapter, 'CREATE TABLE "orphan" (id TEXT PRIMARY KEY, content TEXT)')
    sql(adapter, 'INSERT INTO "orphan" VALUES (%s, %s)', ("keep", "unowned"))
    with pytest.raises(RuntimeError, match="metadata|orphan"):
        adapter.create_collection("orphan", CollectionSchemas.context_collection("orphan", 8),
                                  distance="cosine", sparse_weight=0.0, index_name="default")
    assert sql(adapter, 'SELECT id, content FROM "orphan"') == [("keep", "unowned")]
