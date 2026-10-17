# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real adapter CAS + durable content_only delivery in the approved isolated DB.

Credential-safe entrypoint (no DSN on the command line)::

    .venv-opengauss-validation/bin/python tests/vectordb/test_opengauss_acceptance_helper.py \
        tests/vectordb/test_opengauss_content_reindex_database.py -q --no-cov \
        --tb=short -p no:cacheprovider

Only source FS, lock leases, queue transport and model outputs are substitutes;
all collection inventory, CAS, vectors, postings and keyword searches use SQL.
"""

import asyncio
import json
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_opengauss_acceptance_helper import acceptance_isolation, acceptance_target  # noqa: F401
from test_opengauss_retrieval_database import (  # noqa: F401
    _feature_postings,
    _feature_sql,
    feature_adapter,
    feature_peer,
)

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service import reindex_executor as executor_module
from openviking.service.reindex_executor import ReindexExecutor
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.reindex_msg import ReindexMsg
from openviking.storage.queuefs.reindex_processor import ReindexProcessor
from openviking.storage.vector_ids import vector_record_id
from openviking.storage.viking_vector_index_backend import VIKINGDB_CONTENT_MAX_SIZE
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
OWNER = {"account_id": "a", "user_id": "content-maintainer"}
VECTOR = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
SPARSE = {"content_source_term": 2.5, "stable_vector_term": 1.0}


class RealAdapterStore:
    """Async service boundary only; do not substitute adapter or collection SQL."""

    has_queue_manager = True
    is_closing = False

    def __init__(self, adapter):
        self.adapter = adapter
        self.before_cas = None
        self.cas_calls = []
        self.inventory_pages = []
        self.upsert_calls = []

    async def get_account_backend(self, account_id):
        assert account_id == OWNER["account_id"]
        return SimpleNamespace(
            _mode="opengauss",
            get_content_policy=lambda: self.get_content_policy(ctx=None),
        )

    async def get_content_policy(self, *, ctx):
        enabled = await asyncio.to_thread(lambda: self.adapter.USE_CONTENT_FIELD)
        return {
            "enabled": enabled, "max_size": None, "source": "full_source",
            "atomic_backfill": enabled and callable(getattr(self.adapter, "backfill_content_record", None)),
        }

    async def supports_atomic_content_backfill(self, *, ctx):
        return (await self.get_content_policy(ctx=ctx))["atomic_backfill"]

    async def filter(self, *, filter, ctx, **kwargs):
        rows = await asyncio.to_thread(
            self.adapter.query, filter=And([Eq("account_id", ctx.account_id), filter]), **kwargs
        )
        self.inventory_pages.append(deepcopy(rows))
        return rows

    async def get_strict(self, ids, *, ctx):
        rows = await asyncio.to_thread(self.adapter.get, ids)
        return [row for row in rows if row.get("account_id") == ctx.account_id]

    async def backfill_content_record(self, snapshot, content, *, ctx, repair=False):
        assert snapshot["account_id"] == ctx.account_id
        if self.before_cas is not None:
            callback, self.before_cas = self.before_cas, None
            await callback(snapshot, content)
        changed = await asyncio.to_thread(
            self.adapter.backfill_content_record, snapshot, content,
            filter=Eq("account_id", ctx.account_id), repair=repair,
        )
        self.cas_calls.append((deepcopy(snapshot), content, repair, changed))
        return changed

    async def upsert(self, record, *, ctx, options=None):
        assert record["account_id"] == ctx.account_id
        self.upsert_calls.append(deepcopy(record))
        return (await asyncio.to_thread(self.adapter.upsert, [record]))[0]

    async def enqueue_embedding_msg(self, *args, **kwargs):
        raise AssertionError("content maintenance must not enqueue embeddings")


class ReindexDatabaseEnvironment:
    def __init__(self, adapter, peer, tmp_path, monkeypatch):
        self.adapter = adapter
        self.peer = peer
        self.collection = adapter.retrieval_collection()
        self.root = "viking://resources/content_reindex_" + uuid.uuid4().hex[:12]
        self.ctx = RequestContext(user=UserIdentifier(**OWNER), role=Role.ADMIN)
        self.sources = {}
        self.reads = []
        self.store = RealAdapterStore(adapter)
        self.task_path = tmp_path / "durable-agfs"
        self.task_fs = MockLocalAGFS(root_path=self.task_path)
        self.tracker = TaskTracker(PersistentTaskStore(self.task_fs))
        self.leases = SimpleNamespace(
            pathlock_acquire_tree=AsyncMock(return_value={"lease": "tree"}),
            pathlock_acquire_exact=AsyncMock(return_value={"lease": "file"}),
            pathlock_as_borrowed=AsyncMock(return_value={"borrowed": True}),
            pathlock_release=AsyncMock(),
        )
        self.fs = SimpleNamespace(
            _async_agfs=self.leases,
            _uri_to_path=lambda uri, **kwargs: "/a/" + uri.removeprefix("viking://"),
            stat=AsyncMock(return_value={"isDir": True}),
            read_file=self.read_file,
        )
        self.service = SimpleNamespace(
            viking_fs=self.fs, vikingdb_manager=self.store,
            _vlm_resolver=None, _vector_config_resolver=None,
        )
        monkeypatch.setattr(executor_module, "get_service", lambda: self.service)
        monkeypatch.setattr("openviking.server.dependencies.get_service", lambda: self.service)
        monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: self.fs)
        monkeypatch.setattr(executor_module, "get_task_tracker", lambda: self.tracker)
        monkeypatch.setattr(
            "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: self.tracker
        )
        monkeypatch.setattr(
            "openviking.utils.embedding_utils._resolve_resource_content_type",
            AsyncMock(return_value=None),
        )
        self.model_guard = Mock(side_effect=AssertionError("content_only must not call a model"))
        monkeypatch.setattr(ReindexExecutor, "_semantic_processor_for", self.model_guard)
        self.deliveries = []
        self.queue = SimpleNamespace(REINDEX="Reindex", enqueue=self.enqueue)
        monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: self.queue)

    async def read_file(self, uri, *, ctx):
        assert ctx.account_id == OWNER["account_id"]
        assert uri.startswith(self.root + "/"), "source read escaped the new test root"
        self.reads.append(uri)
        if uri not in self.sources:
            raise FileNotFoundError(uri)
        return self.sources[uri]

    async def enqueue(self, queue, payload):
        assert queue == "Reindex"
        path = f"/deliveries/{len(self.deliveries)}.json"
        self.task_fs.write(path, json.dumps(payload))
        self.deliveries.append(path)

    def reload(self):
        # A new storage client, store and tracker must reread the actual disk.
        self.task_fs = MockLocalAGFS(root_path=self.task_path)
        self.tracker = TaskTracker(PersistentTaskStore(self.task_fs))
        return self.tracker

    def delivery(self, index=-1):
        return json.loads(self.task_fs.read(self.deliveries[index]))

    def record(self, name, *, content=None, source="full source tailkeyword", **fields):
        uri = self.root + "/" + name + ".txt"
        self.sources[uri] = source
        return {
            "id": vector_record_id(OWNER["account_id"], uri, 2),
            "account_id": OWNER["account_id"], "owner_user_id": OWNER["user_id"],
            "uri": uri, "level": 2, "context_type": "resource",
            "abstract": "historical embedding summary", "md5": "stable-source-hash",
            "updated_at": "2026-10-16T00:00:00Z", "content": content,
            "vector": list(VECTOR), "sparse_vector": dict(SPARSE), **fields,
        }

    def seed(self, *records):
        self.adapter.upsert(list(records))
        return [self.adapter.get([record["id"]])[0] for record in records]

    async def message(self, *, force=False, object_type="resource"):
        task = await self.tracker.create("admin_reindex", resource_id=self.root, **OWNER)
        msg = ReindexMsg(
            task_id=task.task_id, uri=self.root, object_type=object_type, mode="content_only",
            role="admin", force=force, _task_work_id="content-root-work", **OWNER,
        )
        await self.enqueue("Reindex", msg.to_dict())
        return self.delivery()

    async def process(self, payload):
        return await ReindexProcessor(self.fs).on_dequeue({"data": payload})

    async def persisted(self, task_id):
        return await TaskTracker(PersistentTaskStore(
            MockLocalAGFS(root_path=self.task_path)
        )).get(task_id, **OWNER)

    def raw_rows(self):
        return _feature_sql(
            self.adapter,
            f'SELECT id, uri, account_id, updated_at, content, "_ov_content_empty", '
            f'vector::text, sparse_vector::text FROM "{self.collection._name}" ORDER BY id',
        )

    def vector_state(self):
        vectors = [(row[0], row[-2], row[-1]) for row in self.raw_rows()]
        indexes = _feature_sql(
            self.adapter,
            "SELECT x.relname, pg_catalog.pg_get_indexdef(i.indexrelid) "
            "FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_class x ON x.oid=i.indexrelid "
            "JOIN pg_am a ON a.oid=x.relam "
            "WHERE n.nspname=current_schema() AND t.relname=%s "
            "AND a.amname IN ('hnsw', 'ivfflat') ORDER BY x.relname",
            (self.collection._name,),
        )
        assert indexes, "the fixture must have a real ANN index"
        postings = _feature_postings(self.adapter)
        assert postings, "the fixture must have real sparse postings"
        return vectors, postings, indexes

    def build_fulltext(self, mode="bm25"):
        self.collection.build_fulltext_index(allow_ddl=True)
        assert self.collection.validate_fulltext_index(mode)

    def hits(self, keyword, mode="bm25"):
        return {
            row["id"] for row in self.adapter.search_by_keywords(
                query=keyword, mode=mode, fields=["content"],
                filter=And([Eq("account_id", "a"), PathScope("uri", self.root)]),
            )
        }

    def assert_inventory_scope(self):
        for page in self.store.inventory_pages:
            assert [row["id"] for row in page] == sorted(row["id"] for row in page)
            for row in page:
                assert len(row["id"]) == 32 and row["id"] not in {"old", "new", "private"}
                assert row["uri"].startswith(self.root + "/")
                assert row["account_id"] == "a"


@pytest.fixture
def reindex_db(feature_adapter, feature_peer, acceptance_isolation, tmp_path, monkeypatch):  # noqa: F811
    target = acceptance_target()
    assert target.port == 35436
    collection = feature_adapter.retrieval_collection()
    assert _feature_sql(feature_adapter, "SHOW sql_compatibility") == [("A",)]
    assert _feature_sql(feature_adapter, "SELECT current_schema()") == [(acceptance_isolation,)]
    collection.update(fields=[
        {"FieldName": "level", "FieldType": "int64"},
        {"FieldName": "context_type", "FieldType": "string"},
        {"FieldName": "abstract", "FieldType": "text"},
        {"FieldName": "owner_user_id", "FieldType": "string"},
        {"FieldName": "md5", "FieldType": "string"},
    ])
    capability = feature_adapter.probe_capabilities()["bm25"]
    if capability["state"] != "available":
        if capability["state"] == "unsupported":
            pytest.skip(f"dedicated instance does not advertise bm25: {capability}")
        pytest.fail(f"dedicated instance bm25 probe failed: {capability}", pytrace=False)
    collection.configure_fulltext("bm25")
    state = collection.migrate_content_schema(allow_ddl=True)
    assert state["schema_version"] == 2
    assert collection.backfill_content_batch(lambda row: "fixture baseline text")["done"]
    collection.create_sparse_index(sparse_weight=0.5)
    env = ReindexDatabaseEnvironment(feature_adapter, feature_peer, tmp_path, monkeypatch)
    original = feature_adapter.get(["old", "new", "private"])
    yield env
    env.assert_inventory_scope()
    env.model_guard.assert_not_called()
    assert feature_adapter.get(["old", "new", "private"]) == original


async def completed(env, payload):
    assert (await env.process(payload)).outcome.value == "success"
    task = await env.persisted(payload["task_id"])
    assert task.status == TaskStatus.COMPLETED
    checkpoint = task.meta["content_only_checkpoint"]
    assert checkpoint["complete"] is True and checkpoint["error"] is None
    assert checkpoint["scope"]["uri"] == env.root
    assert task.result["rebuilt_records"] == task.result["deleted_records"] == 0
    assert task.result["status"] == "completed"
    return task


async def test_online_source_tail_and_force_summary_repair_preserve_vectors(reindex_db):
    env = reindex_db
    online_tail, repaired_tail = "onlinesourcetailunique", "repairedsourcetailunique"
    source = "retained source body " * (VIKINGDB_CONTENT_MAX_SIZE // 21 + 10) + online_tail
    assert len(source) > VIKINGDB_CONTENT_MAX_SIZE
    online = env.record("online", source=source)
    online_id = online["id"]
    online_data = {key: value for key, value in online.items() if key not in {"id", "content", "vector", "sparse_vector"}}
    embed = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=VECTOR, sparse_vector=SPARSE)),
    )
    handler = TextEmbeddingHandler(env.store, SimpleNamespace(bind=lambda account: embed))
    outcome = await handler.on_dequeue({"data": EmbeddingMsg(
        message="short embedding summary", context_data=online_data,
    ).to_json()})
    assert outcome.outcome.value == "success"
    assert env.adapter.get([online_id])[0]["content"] == source
    assert embed.embed_async.call_args.args[0] == "short embedding summary"
    historical_source = "complete source document " * (VIKINGDB_CONTENT_MAX_SIZE // 25 + 10) + repaired_tail
    assert len(historical_source) > VIKINGDB_CONTENT_MAX_SIZE
    historical = env.record(
        "historical", content="historical summary only", source=historical_source,
    )
    env.seed(historical)
    env.build_fulltext()
    before = env.vector_state()
    assert env.hits(online_tail) == {online_id}
    assert env.hits(repaired_tail) == set()
    default = await completed(env, await env.message())
    assert default.result["content_updated_records"] == 0
    assert default.result["content_skipped_records"] == 2
    assert env.adapter.get([historical["id"]])[0]["content"] == "historical summary only"
    assert env.store.cas_calls == []
    force = await completed(env, await env.message(force=True))
    assert force.result["content_updated_records"] == 2
    assert all(repair and changed for _, _, repair, changed in env.store.cas_calls)
    assert env.hits(online_tail) == {online_id}
    assert env.hits(repaired_tail) == {historical["id"]}
    assert env.adapter.get([historical["id"]])[0]["content"] == historical_source
    assert env.adapter.get([online_id])[0]["content"] == source
    assert env.vector_state() == before
    assert len(env.store.upsert_calls) == 1
    assert embed.embed_async.await_count == 1


async def test_memory_hidden_trailer_online_preloaded_and_force_repair(reindex_db):
    env = reindex_db
    env.root = (
        "viking://user/content-maintainer/memories/content_reindex_" + uuid.uuid4().hex[:12]
    )
    assert ReindexExecutor()._infer_target_type(env.root) == "memory"
    env.collection.configure_fulltext("bm25")
    hidden = "memorymetadatasecretonly"
    bodies, records = {}, {}
    for name in ("online", "preloaded", "historical"):
        tail = name + "memoryvisibletailunique"
        body = "complete visible memory body " * (VIKINGDB_CONTENT_MAX_SIZE // 28 + 10) + tail
        assert len(body) > VIKINGDB_CONTENT_MAX_SIZE
        raw = body + '\n\n<!-- MEMORY_FIELDS\n' + json.dumps({"internal_probe": hidden}) + '\n-->'
        bodies[name] = body
        records[name] = env.record(name, source=raw, context_type="memory")
    historical = records["historical"]
    historical["content"] = env.sources[historical["uri"]]
    env.seed(historical)
    env.build_fulltext()
    assert hidden in env.adapter.get([historical["id"]])[0]["content"]
    # Prove a searchable raw-trailer leak before repairing the stored content.
    polluted_hits = {historical["id"]}
    assert env.hits(hidden) == polluted_hits
    embed = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=VECTOR, sparse_vector=SPARSE)),
    )
    handler = TextEmbeddingHandler(env.store, SimpleNamespace(bind=lambda account: embed))
    for name in ("online", "preloaded"):
        record = records[name]
        data = {
            key: value for key, value in record.items()
            if key not in {"id", "content", "vector", "sparse_vector"}
        }
        if name == "preloaded":
            data["_materialized_content"] = env.sources[record["uri"]]
        outcome = await handler.on_dequeue({"data": EmbeddingMsg(
            message="short independent memory summary", context_data=data,
        ).to_json()})
        assert outcome.outcome.value == "success"
        stored = env.adapter.get([record["id"]])[0]
        assert stored["content"] == bodies[name]
        assert hidden not in stored["content"] and "MEMORY_FIELDS" not in stored["content"]
        assert "_materialized_content" not in env.store.upsert_calls[-1]
        assert env.hits(hidden) == polluted_hits
        assert env.hits(name + "memoryvisibletailunique") == {record["id"]}
    assert env.reads == [records["online"]["uri"]]
    assert embed.embed_async.await_count == 2
    assert all(
        call.args[0] == "short independent memory summary"
        for call in embed.embed_async.call_args_list
    )
    before = env.vector_state()
    default = await completed(env, await env.message(object_type="memory"))
    assert default.result["content_updated_records"] == 0
    assert default.result["content_skipped_records"] == 3
    assert env.hits(hidden) == polluted_hits
    assert env.store.cas_calls == []
    force = await completed(env, await env.message(force=True, object_type="memory"))
    assert force.result["object_type"] == "memory"
    assert force.result["content_updated_records"] == 3
    assert force.result["content_skipped_records"] == 0
    assert len(env.store.cas_calls) == 3
    assert all(repair and changed for _, _, repair, changed in env.store.cas_calls)
    assert env.hits(hidden) == set()
    raw_rows = {row[0]: row for row in env.raw_rows()}
    for name, record in records.items():
        assert env.adapter.get([record["id"]])[0]["content"] == bodies[name]
        assert raw_rows[record["id"]][4] == bodies[name]
        assert env.hits(name + "memoryvisibletailunique") == {record["id"]}
    assert env.vector_state() == before
    assert len(env.store.upsert_calls) == 2
    assert embed.embed_async.await_count == 2


async def test_missing_content_and_a_mode_empty_marker_roundtrip(reindex_db):
    env = reindex_db
    text = env.record("missing", source="complete original source missingtailunique")
    empty = env.record("empty", source="")
    protected = env.record("protected", content="preserve nonnull", source="must not replace")
    env.seed(text, empty, protected)
    before = env.vector_state()
    task = await completed(env, await env.message())
    assert task.result["scanned_records"] == 3
    assert task.result["content_updated_records"] == 2
    assert task.result["content_skipped_records"] == 1
    assert env.adapter.get([text["id"]])[0]["content"].endswith("missingtailunique")
    empty_snapshot = env.adapter.get([empty["id"]])[0]
    assert empty_snapshot["content"] == "" and "_ov_content_empty" not in empty_snapshot
    raw = {row[0]: row for row in env.raw_rows()}[empty["id"]]
    assert raw[4] is None and raw[5] is True
    assert env.adapter.get([protected["id"]])[0]["content"] == "preserve nonnull"
    stale_null = {**empty_snapshot, "content": None}
    assert not env.adapter.backfill_content_record(stale_null, "must not overwrite explicit empty")
    assert env.adapter.get([empty["id"]])[0]["content"] == ""
    assert env.adapter.backfill_content_record(empty_snapshot, "", repair=True)
    env.build_fulltext()
    assert env.hits("missingtailunique") == {text["id"]}
    env.reload()
    again = await completed(env, await env.message())
    assert again.result["content_updated_records"] == 0
    assert again.result["content_skipped_records"] == 3
    assert env.vector_state() == before
    assert env.store.upsert_calls == []


@pytest.mark.parametrize("force", [False, True])
async def test_committed_cas_before_checkpoint_survives_disk_restart_and_redelivery(
    reindex_db, monkeypatch, force,
):
    env = reindex_db
    record = env.record("crash", content="legacy summary" if force else None,
                        source="full source committedtailunique")
    env.seed(record)
    before = env.vector_state()
    payload = await env.message(force=force)
    patch_task = env.tracker.patch_task
    crashed = False

    async def crash_before_checkpoint(task_id, patch, **kwargs):
        nonlocal crashed
        checkpoint = (patch.meta or {}).get("content_only_checkpoint", {})
        if not crashed and checkpoint.get("after_id") == record["id"]:
            crashed = True
            assert env.adapter.get([record["id"]])[0]["content"].endswith("committedtailunique")
            raise asyncio.CancelledError("process stopped after SQL commit, before durable cursor")
        return await patch_task(task_id, patch, **kwargs)

    monkeypatch.setattr(env.tracker, "patch_task", crash_before_checkpoint)
    with pytest.raises(asyncio.CancelledError):
        await env.process(payload)
    assert crashed
    pending = await env.persisted(payload["task_id"])
    assert pending.status == TaskStatus.RUNNING
    assert pending.meta["content_only_checkpoint"]["after_id"] == ""
    assert pending.meta["content_only_checkpoint"]["complete"] is False
    assert env.store.cas_calls[-1][-1] is True
    env.reload()
    restored_delivery = env.delivery(0)
    assert restored_delivery["_task_work_id"] == payload["_task_work_id"]
    task = await completed(env, restored_delivery)
    assert task.result["scanned_records"] == 1
    assert task.result["content_updated_records"] == int(force)
    assert task.result["content_skipped_records"] == int(not force)
    assert env.adapter.get([record["id"]])[0]["content"].endswith("committedtailunique")
    env.build_fulltext()
    assert env.hits("committedtailunique") == {record["id"]}
    assert env.vector_state() == before
    calls, pages = len(env.store.cas_calls), len(env.store.inventory_pages)
    env.reload()
    assert (await env.process(restored_delivery)).outcome.value == "success"
    assert (len(env.store.cas_calls), len(env.store.inventory_pages)) == (calls, pages)
    assert env.store.upsert_calls == []


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("changed", ["account_id", "uri", "updated_at", "content", "empty_marker", "deleted", "filter"])
async def test_real_cas_rejects_stale_revision_scope_marker_and_deletion(reindex_db, repair, changed):
    env = reindex_db
    initial = None if changed == "empty_marker" or not repair else "historical summary"
    record = env.record("cas_guard", content=initial)
    snapshot = env.seed(record)[0]
    original_snapshot = deepcopy(snapshot)
    vectors = env.vector_state()
    if changed == "deleted":
        assert env.peer.delete(ids=[record["id"]]) == 1
    elif changed != "filter":
        patch = {
            "account_id": {"account_id": "other"},
            "uri": {"uri": env.root + "-sibling/moved.txt"},
            "updated_at": {"updated_at": "2026-10-16T00:00:01Z"},
            "content": {"content": "concurrent authoritative body"},
            "empty_marker": {"content": ""},
        }[changed]
        env.peer.update_data([{"id": record["id"], **patch}])
    before = env.raw_rows()
    if changed == "empty_marker":
        raw = next(row for row in before if row[0] == record["id"])
        assert raw[4] is None and raw[5] is True
        assert snapshot["content"] is None
    scope = Eq("account_id", "denied" if changed == "filter" else "a")
    assert env.adapter.backfill_content_record(snapshot, "stale source must not win", filter=scope, repair=repair) is False
    assert env.raw_rows() == before
    assert snapshot == original_snapshot
    if changed == "deleted":
        assert env.adapter.get([record["id"]]) == []
        assert not any(row[0] == record["id"] for row in _feature_postings(env.adapter))
    else:
        assert env.vector_state() == vectors
    assert env.store.upsert_calls == []


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("resolution", ["populated", "still_null", "deleted"])
async def test_real_cas_conflict_requeues_pending_task_and_refreshes_after_disk_restart(
    reindex_db, force, resolution,
):
    env = reindex_db
    record = env.record("pending", content="legacy summary" if force else None,
                        source="obsolete source must never win")
    env.seed(record)
    before = env.vector_state()
    payload = await env.message(force=force)

    async def update_after_inventory(snapshot, stale_source):
        assert stale_source == "obsolete source must never win"
        patch = {"id": record["id"], "updated_at": "2026-10-16T00:00:02Z"}
        patch["content"] = "concurrent authoritative concurrenttailunique" if resolution == "populated" else None
        await asyncio.to_thread(env.peer.update_data, [patch])
        env.sources[record["uri"]] = "new authoritative source refreshedtailunique"

    env.store.before_cas = update_after_inventory
    assert (await env.process(payload)).outcome.value == "requeued"
    assert env.store.cas_calls[-1][-1] is False
    pending = await env.persisted(payload["task_id"])
    assert pending.status == TaskStatus.RUNNING
    saved = pending.meta["content_only_checkpoint"]
    assert saved["after_id"] == "" and saved["complete"] is False
    assert saved["error"]["type"] == "ContentBackfillConflictError"
    assert saved["error"]["record_id"] == record["id"]
    current = env.adapter.get([record["id"]])[0]
    assert current["content"] == ("concurrent authoritative concurrenttailunique" if resolution == "populated" else None)
    assert env.vector_state() == before
    if resolution == "deleted":
        assert env.peer.delete(ids=[record["id"]]) == 1
    env.reload()
    replacement = env.delivery()
    assert replacement["content_retry_count"] == 1
    assert replacement["task_id"] == payload["task_id"]
    assert replacement["_task_work_id"] == payload["_task_work_id"]
    task = await completed(env, replacement)
    assert task.meta["content_only_checkpoint"]["after_id"] == record["id"]
    if resolution == "deleted":
        assert task.result["content_updated_records"] == 0
        assert task.result["content_skipped_records"] == 1
        assert env.adapter.get([record["id"]]) == []
        assert not any(row[0] == record["id"] for row in _feature_postings(env.adapter))
    else:
        should_write = force or resolution == "still_null"
        assert task.result["content_updated_records"] == int(should_write)
        assert task.result["content_skipped_records"] == int(not should_write)
        expected = env.sources[record["uri"]] if should_write else current["content"]
        assert env.adapter.get([record["id"]])[0]["content"] == expected
        assert env.vector_state() == before
        env.build_fulltext()
        assert env.hits("refreshedtailunique" if should_write else "concurrenttailunique") == {record["id"]}
    assert env.store.upsert_calls == []
