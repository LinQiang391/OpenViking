import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service import reindex_executor as module
from openviking.service.reindex_executor import ReindexExecutor
from openviking.service.task_domain import TaskPatch
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import bind_task_context
from openviking.storage.expr import And, Eq, Or, PathScope, Range
from openviking.storage.queuefs.reindex_msg import ReindexMsg
from openviking.storage.queuefs.reindex_processor import ReindexProcessor
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs

ROOT = "viking://resources/demo"
OWNER = {"account_id": "acct", "user_id": "actor"}


def rid(value):
    return value.rjust(32, "0")


def record(record_id, **fields):
    return {
        "id": rid(record_id),
        "uri": ROOT + "/" + record_id + ".txt",
        "level": 2,
        "account_id": "acct",
        "context_type": "resource",
        "updated_at": "revision",
        "content": None,
        **fields,
    }


@pytest.fixture
def env(monkeypatch):
    ctx = RequestContext(
        user=UserIdentifier("acct", "actor"),
        role=Role.ADMIN,
        group_ids=("group",),
        actor_peer_id="peer",
    )
    agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value={"lease": "tree"}),
        pathlock_acquire_exact=AsyncMock(return_value={"lease": "exact"}),
        pathlock_as_borrowed=AsyncMock(return_value={"borrowed": True}),
        pathlock_release=AsyncMock(),
        pathlock_to_handoff=AsyncMock(return_value={"ref": "handoff"}),
        pathlock_handoff=AsyncMock(),
    )
    fs = SimpleNamespace(
        _async_agfs=agfs,
        _uri_to_path=Mock(return_value="/acct/resources/demo"),
        stat=AsyncMock(return_value={"isDir": True}),
        read_file=AsyncMock(return_value="full source"),
    )
    store = SimpleNamespace(
        has_queue_manager=True,
        get_content_policy=AsyncMock(return_value={
            "enabled": True, "max_size": None, "source": "full_source", "atomic_backfill": True,
        }),
        supports_atomic_content_backfill=AsyncMock(return_value=True),
        filter=AsyncMock(side_effect=[[record("a"), record("b")], []]),
        backfill_content_record=AsyncMock(return_value=True),
        get_strict=AsyncMock(return_value=[record("b")]),
        upsert=Mock(side_effect=AssertionError("must not insert")),
        enqueue_embedding_msg=Mock(side_effect=AssertionError("must not embed")),
    )
    service = SimpleNamespace(
        viking_fs=fs,
        vikingdb_manager=store,
        _vlm_resolver=None,
        _vector_config_resolver=None,
    )
    monkeypatch.setattr(module, "get_service", lambda: service)
    monkeypatch.setattr("openviking.server.dependencies.get_service", lambda: service)
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.utils.embedding_utils._resolve_resource_content_type",
        AsyncMock(return_value=None),
    )
    task_agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(task_agfs))
    monkeypatch.setattr(module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: tracker
    )
    executor = ReindexExecutor()
    executor._semantic_processor_for = Mock(side_effect=AssertionError("must not call model"))
    return SimpleNamespace(
        ctx=ctx,
        agfs=agfs,
        fs=fs,
        store=store,
        service=service,
        tracker=tracker,
        task_agfs=task_agfs,
        executor=executor,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query", "disabled", "unsupported", "cancelled"])
@pytest.mark.parametrize("handoff", [False, True])
async def test_consumer_releases_transferred_lease_when_capability_check_fails(env, failure, handoff):
    task = await make_task(env)
    lease = {"lease": "adopted" if handoff else "tree"}
    env.agfs.pathlock_adopt = AsyncMock(return_value=lease)
    if failure == "query":
        env.store.get_content_policy.side_effect = RuntimeError("database unavailable")
        error = RuntimeError
    elif failure == "disabled":
        env.store.get_content_policy.return_value = {"enabled": False}
        error = InvalidArgumentError
    elif failure == "unsupported":
        env.store.supports_atomic_content_backfill.return_value = False
        error = InvalidArgumentError
    else:
        env.store.get_content_policy.side_effect = asyncio.CancelledError()
        error = asyncio.CancelledError
    msg = ReindexMsg(
        task_id=task.task_id, uri=ROOT, object_type="resource", mode="content_only",
        role="admin", lock_handoff={"ref": "handoff"} if handoff else None, **OWNER,
    )

    with pytest.raises(error):
        await ReindexProcessor(env.fs).on_dequeue({"data": msg.to_dict()})

    if handoff:
        env.agfs.pathlock_adopt.assert_awaited_once_with({"ref": "handoff"})
        env.agfs.pathlock_acquire_tree.assert_not_awaited()
    else:
        env.agfs.pathlock_acquire_tree.assert_awaited_once()
    env.agfs.pathlock_release.assert_awaited_once_with(lease)
    env.agfs.pathlock_as_borrowed.assert_not_awaited()
    env.store.filter.assert_not_awaited()
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_check_failure_without_inherited_lease_does_not_acquire(env):
    env.store.get_content_policy.side_effect = RuntimeError("database unavailable")
    task = await make_task(env)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await run_task(env, task)
    env.agfs.pathlock_acquire_tree.assert_not_awaited()
    env.agfs.pathlock_acquire_exact.assert_not_awaited()
    env.agfs.pathlock_release.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stat_error", "stat_cancelled", "queue_unavailable"])
async def test_inherited_lease_is_protected_during_remaining_preflight(env, failure):
    lease = {"lease": "inherited"}
    if failure == "stat_error":
        env.fs.stat.side_effect = RuntimeError("stat failed")
        error = RuntimeError
    elif failure == "stat_cancelled":
        env.fs.stat.side_effect = asyncio.CancelledError()
        error = asyncio.CancelledError
    else:
        env.store.has_queue_manager = False
        error = OpenVikingError
    task = await make_task(env)
    with pytest.raises(error):
        await run_task(env, task, existing_lease=lease)
    env.agfs.pathlock_release.assert_awaited_once_with(lease)
    env.agfs.pathlock_acquire_tree.assert_not_awaited()
    env.agfs.pathlock_acquire_exact.assert_not_awaited()


async def make_task(env):
    return await env.tracker.create("admin_reindex", resource_id=ROOT, **OWNER)


async def run_task(env, task, **kwargs):
    with bind_task_context(task.task_id, **OWNER):
        return await env.executor._run(
            uri=ROOT, object_type="resource", mode="content_only", ctx=env.ctx, **kwargs
        )


@pytest.mark.asyncio
async def test_content_only_keyset_checkpoint_and_no_rebuild(env):
    task = await make_task(env)
    result = await run_task(env, task)
    assert result["status"] == "completed"
    assert result["content_updated_records"] == 2
    assert result["scanned_records"] == 2
    assert result["rebuilt_records"] == result["deleted_records"] == 0
    assert "ready" not in result
    env.store.upsert.assert_not_called()
    env.store.enqueue_embedding_msg.assert_not_called()
    env.executor._semantic_processor_for.assert_not_called()
    calls = env.store.filter.call_args_list
    assert calls[0].kwargs["filter"] == And(
        [Eq("account_id", "acct"), Or([Eq("uri", ROOT), PathScope("uri", ROOT)])]
    )
    assert calls[1].kwargs["filter"].conds[-1] == Range("id", gt=rid("b"))
    assert all(c.kwargs["offset"] == 0 and c.kwargs["order_by"] == "id" for c in calls)
    checkpoint = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert checkpoint["after_id"] == rid("b") and checkpoint["complete"] is True
    assert checkpoint["scope"]["account_id"] == "acct"
    assert checkpoint["scope"]["uri"] == ROOT
    assert env.task_agfs.write_calls
    env.agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", [1, 2, 3])
async def test_content_only_awaits_owner_scoped_cancellation(env, cancel_at):
    task = await make_task(env)
    checks = 0

    async def cancellation_requested(task_id, *, account_id, user_id):
        nonlocal checks
        assert task_id == task.task_id
        assert {"account_id": account_id, "user_id": user_id} == OWNER
        checks += 1
        return checks == cancel_at

    env.tracker.is_cancellation_requested = AsyncMock(side_effect=cancellation_requested)
    with pytest.raises(asyncio.CancelledError):
        await run_task(env, task)
    assert env.tracker.is_cancellation_requested.await_count == cancel_at
    assert env.store.backfill_content_record.await_count == max(0, cancel_at - 2)
    saved = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert saved["after_id"] == (rid("a") if cancel_at == 3 else "")
    assert not saved["complete"]
    env.agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkpoint_patch_preserves_unrelated_task_metadata(env):
    task = await make_task(env)
    await env.tracker.patch_task(
        task.task_id, TaskPatch(meta={"submission_hash": "retained"}), **OWNER
    )
    await run_task(env, task)
    restored = await TaskTracker(PersistentTaskStore(env.task_agfs)).get(task.task_id, **OWNER)
    assert restored.meta["submission_hash"] == "retained"
    assert restored.meta["content_only_checkpoint"]["complete"]
    assert restored.stage == "content_only"


@pytest.mark.asyncio
async def test_crash_reloads_checkpoint_from_persistent_task_not_memory(env, monkeypatch):
    task = await make_task(env)
    count = 0

    async def interrupted_cas(snapshot, content, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise asyncio.CancelledError()
        return True

    env.store.backfill_content_record.side_effect = interrupted_cas
    with pytest.raises(asyncio.CancelledError):
        await run_task(env, task)
    fresh = TaskTracker(PersistentTaskStore(env.task_agfs))
    restored = await fresh.get(task.task_id, **OWNER)
    assert restored.meta["content_only_checkpoint"]["after_id"] == rid("a")
    monkeypatch.setattr(module, "get_task_tracker", lambda: fresh)
    env.store.filter = AsyncMock(side_effect=[[record("b")], []])
    env.store.backfill_content_record.side_effect = None
    result = await run_task(env, task)
    assert result["scanned_records"] == 2 and result["content_updated_records"] == 2
    assert env.store.filter.call_args_list[0].kwargs["filter"].conds[-1] == Range("id", gt=rid("a"))
    assert env.store.backfill_content_record.await_count == 3
    assert env.agfs.pathlock_release.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"force": True}, {"recursive": False}])
async def test_checkpoint_cannot_be_reused_for_different_request(env, change):
    task = await make_task(env)
    await run_task(env, task)
    with pytest.raises(RuntimeError, match="scope mismatch"):
        await run_task(env, task, **change)
    assert env.store.filter.await_count == 2


@pytest.mark.asyncio
async def test_completed_checkpoint_replay_performs_no_writes(env):
    task = await make_task(env)
    first = await run_task(env, task)
    second = await run_task(env, task)
    assert first["content_updated_records"] == second["content_updated_records"] == 2
    assert env.store.backfill_content_record.await_count == 2
    assert env.store.filter.await_count == 2


@pytest.mark.asyncio
async def test_force_is_explicit_non_null_cas_repair(env):
    task = await make_task(env)
    snapshot = record("a", content="historical summary")
    env.store.filter.side_effect = [[snapshot], []]
    result = await run_task(env, task, force=True)
    assert result["content_updated_records"] == 1
    env.store.backfill_content_record.assert_awaited_once_with(
        snapshot, "full source", ctx=env.ctx, repair=True
    )
    assert snapshot["content"] == "historical summary"


@pytest.mark.asyncio
async def test_non_force_skip_but_cas_conflict_stops_before_cursor(env):
    task = await make_task(env)
    env.store.filter.side_effect = [[record("a", content="keep"), record("b")], []]
    env.store.backfill_content_record.return_value = False
    result = await run_task(env, task)
    assert result["content_updated_records"] == 0
    assert result["status"] == "retry_pending"
    assert result["content_skipped_records"] == 1
    assert result["content_error"]["type"] == "ContentBackfillConflictError"
    saved = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert saved["after_id"] == rid("a") and not saved["complete"]
    assert env.store.backfill_content_record.await_count == 1
    env.store.get_strict.assert_awaited_once_with([rid("b")], ctx=env.ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,error",
    [
        ([record("b"), record("a")], RuntimeError),
        ([record("a"), record("a")], RuntimeError),
        ([record("a", account_id="other")], PermissionError),
        ([record("a", uri=ROOT + "-sibling/a.txt")], PermissionError),
    ],
)
async def test_inventory_fails_closed_before_any_page_write(env, page, error):
    task = await make_task(env)
    env.store.filter.side_effect = [page]
    with pytest.raises(error):
        await run_task(env, task)
    env.store.backfill_content_record.assert_not_awaited()
    env.agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonrecursive_uses_exact_uri(env):
    task = await make_task(env)
    env.store.filter.side_effect = [[record("a", uri=ROOT)], []]
    await run_task(env, task, recursive=False)
    assert env.store.filter.call_args_list[0].kwargs["filter"] == And(
        [Eq("account_id", "acct"), Eq("uri", ROOT)]
    )


@pytest.mark.asyncio
async def test_missing_source_reports_partial_without_creating_rows(env):
    task = await make_task(env)
    env.fs.read_file.side_effect = FileNotFoundError("gone")
    result = await run_task(env, task)
    assert result["status"] == "retry_pending" and result["failed_records"] == 1
    assert result["content_updated_records"] == 0
    assert len(result["warnings"]) == 1
    saved = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert saved["after_id"] == "" and saved["error"]["record_id"] == rid("a")
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_only_requires_persistent_task_and_supported_policy(env):
    with pytest.raises(RuntimeError, match="durable Task"):
        await env.executor._run(uri=ROOT, object_type="resource", mode="content_only", ctx=env.ctx)
    task = await make_task(env)
    env.store.get_content_policy.return_value = {"enabled": False}
    with pytest.raises(InvalidArgumentError, match="content storage enabled"):
        await run_task(env, task)
    env.store.filter.assert_not_awaited()
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [True, False])
@pytest.mark.parametrize("unsupported", ["disabled", "legacy", "missing", "mock", "mock_policy"])
async def test_unsupported_content_only_rejects_before_task_or_queue(
    env, monkeypatch, wait, unsupported
):
    if unsupported == "disabled":
        env.store.get_content_policy.return_value = {"enabled": False}
    elif unsupported == "legacy":
        env.store.get_content_policy.return_value = {
            "enabled": True, "source": "legacy", "atomic_backfill": False,
        }
        env.store.supports_atomic_content_backfill.return_value = False
    elif unsupported == "missing":
        del env.store.supports_atomic_content_backfill
    elif unsupported == "mock":
        env.store.supports_atomic_content_backfill = MagicMock()
    else:
        env.store.get_content_policy.return_value = MagicMock()
    tracker_getter = Mock(return_value=env.tracker)
    monkeypatch.setattr(module, "get_task_tracker", tracker_getter)
    queue_getter = Mock()
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", queue_getter)
    env.tracker.create_if_no_running = AsyncMock()
    with pytest.raises(InvalidArgumentError, match="content_only requires"):
        await env.executor.execute(uri=ROOT, mode="content_only", wait=wait, ctx=env.ctx)
    tracker_getter.assert_not_called()
    env.tracker.create_if_no_running.assert_not_awaited()
    queue_getter.assert_not_called()
    assert not env.task_agfs.write_calls
    env.fs.stat.assert_not_awaited()
    env.agfs.pathlock_acquire_tree.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_task", [False, True])
async def test_nested_content_only_checks_support_before_execution(env, bound_task):
    env.store.supports_atomic_content_backfill.return_value = False
    env.executor._run = AsyncMock()
    if bound_task:
        with bind_task_context("existing", **OWNER):
            with pytest.raises(InvalidArgumentError, match="atomic content backfill"):
                await env.executor.execute_in_current_task(uri=ROOT, mode="content_only", ctx=env.ctx)
    else:
        with pytest.raises(InvalidArgumentError, match="atomic content backfill"):
            await env.executor.execute_in_current_task(uri=ROOT, mode="content_only", ctx=env.ctx)
    env.executor._run.assert_not_awaited()
    assert not env.task_agfs.write_calls


@pytest.mark.asyncio
async def test_execution_rechecks_content_only_support_before_io(env):
    env.store.supports_atomic_content_backfill.return_value = False
    with pytest.raises(InvalidArgumentError, match="atomic content backfill"):
        await env.executor._run(uri=ROOT, object_type="resource", mode="content_only", ctx=env.ctx)
    env.fs.stat.assert_not_awaited()
    env.store.filter.assert_not_awaited()
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vectors_only", "semantic_and_vectors"])
async def test_existing_modes_do_not_probe_content_backfill_support(env, mode):
    env.store.get_content_policy.side_effect = AssertionError("must not probe content policy")
    env.store.supports_atomic_content_backfill.side_effect = AssertionError("must not probe CAS")
    env.executor._run = AsyncMock(return_value={"status": "completed"})
    with bind_task_context("existing", **OWNER):
        assert await env.executor.execute_in_current_task(uri=ROOT, mode=mode, ctx=env.ctx) == {
            "status": "completed"
        }
    env.store.get_content_policy.assert_not_awaited()
    env.store.supports_atomic_content_backfill.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vectors_only", "semantic_and_vectors"])
@pytest.mark.parametrize("wait", [True, False])
async def test_public_existing_modes_keep_task_and_queue_flow(env, monkeypatch, mode, wait):
    env.store.get_content_policy.side_effect = AssertionError("must not probe content policy")
    env.store.supports_atomic_content_backfill.side_effect = AssertionError("must not probe CAS")
    env.tracker.wait = AsyncMock(return_value=SimpleNamespace(
        status=TaskStatus.COMPLETED, result={"status": "completed"},
    ))
    env.executor._run = AsyncMock(side_effect=AssertionError("must use durable root"))
    queue = SimpleNamespace(REINDEX="Reindex", enqueue=AsyncMock())
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    result = await env.executor.execute(uri=ROOT, mode=mode, wait=wait, ctx=env.ctx)
    assert result["status"] == ("completed" if wait else "accepted")
    assert env.tracker.wait.await_count == int(wait)
    queue.enqueue.assert_awaited_once()
    env.executor._run.assert_not_awaited()
    env.store.get_content_policy.assert_not_awaited()
    env.store.supports_atomic_content_backfill.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_wait_uses_durable_queue_and_original_identity(env, monkeypatch):
    queue = SimpleNamespace(REINDEX="Reindex", enqueue=AsyncMock())
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    env.tracker.wait = AsyncMock(return_value=SimpleNamespace(
        status=TaskStatus.COMPLETED, result={"status": "completed"},
    ))
    result = await env.executor.execute(
        uri=ROOT, mode="content_only", wait=True, force=True, ctx=env.ctx
    )
    assert result == {"status": "completed"}
    payload = queue.enqueue.call_args.args[1]
    msg = ReindexMsg.from_dict(payload)
    assert msg.mode == "content_only" and msg.force is True
    restored = ReindexProcessor._ctx(msg)
    assert restored.account_id == env.ctx.account_id
    assert restored.group_ids == env.ctx.group_ids and restored.actor_peer_id == "peer"
    env.agfs.pathlock_handoff.assert_awaited_once()
    env.tracker.wait.assert_awaited_once()
    with pytest.raises(OpenVikingError, match="already has a reindex"):
        await env.executor.execute(uri=ROOT, mode="content_only", wait=False, ctx=env.ctx)
    assert queue.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_processor_retries_failure_from_persistent_tracker_and_message(
    env, monkeypatch, tmp_path
):
    from tests.utils.mock_agfs import MockLocalAGFS

    env.tracker = TaskTracker(PersistentTaskStore(MockLocalAGFS(root_path=tmp_path)))
    monkeypatch.setattr(module, "get_task_tracker", lambda: env.tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: env.tracker
    )
    task = await make_task(env)
    env.fs.read_file.side_effect = FileNotFoundError("gone")
    processor = ReindexProcessor(env.fs)
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "owned"})
    msg = ReindexMsg(
        task_id=task.task_id,
        uri=ROOT,
        object_type="resource",
        mode="content_only",
        role="admin",
        **OWNER,
    )
    queue = SimpleNamespace(REINDEX="Reindex", enqueue=AsyncMock())
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    monkeypatch.setattr("openviking.storage.queuefs.reindex_processor.asyncio.sleep", AsyncMock())
    msg._task_work_id = "root-work"
    outcome = await processor.on_dequeue({"data": msg.to_dict()})
    assert outcome.outcome.value == "requeued"
    pending = await env.tracker.get(task.task_id, **OWNER)
    assert pending.status == TaskStatus.RUNNING
    assert pending.meta["content_only_checkpoint"]["error"]["record_id"] == rid("a")
    replacement = queue.enqueue.call_args.args[1]
    assert replacement["task_id"] == task.task_id and replacement["_task_work_id"] == "root-work"
    assert replacement["content_retry_count"] == 1
    message_file = tmp_path / "reindex-delivery.json"
    message_file.write_text(json.dumps(replacement), encoding="utf-8")
    fresh = TaskTracker(PersistentTaskStore(MockLocalAGFS(root_path=tmp_path)))
    replacement = json.loads(message_file.read_text(encoding="utf-8"))
    monkeypatch.setattr(module, "get_task_tracker", lambda: fresh)
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: fresh
    )
    env.store.get_strict.return_value = [record("a")]
    env.store.filter.side_effect = [[record("b")], []]
    env.fs.read_file.side_effect = None
    recovered = ReindexProcessor(env.fs)
    recovered._adopt_or_reacquire = AsyncMock(return_value={"lease": "reacquired"})
    assert (await recovered.on_dequeue({"data": replacement})).outcome.value == "success"
    terminal = await fresh.get(task.task_id, **OWNER)
    assert terminal.status == TaskStatus.COMPLETED
    assert terminal.result["content_updated_records"] == 2
    assert terminal.result["status"] == "completed"
    assert terminal.result["content_error"] is None
    assert terminal.meta["content_only_checkpoint"]["complete"]
    env.store.get_strict.assert_awaited_once()
    assert env.store.filter.call_args_list[1].kwargs["filter"].conds[-1] == Range("id", gt=rid("a"))


@pytest.mark.asyncio
async def test_processor_retry_limit_seals_failure_but_retains_unadvanced_checkpoint(env, monkeypatch):
    task = await make_task(env)
    env.fs.read_file.return_value = None
    processor = ReindexProcessor(env.fs)
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "owned"})
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: env.tracker
    )
    queue = SimpleNamespace(REINDEX="Reindex", enqueue=AsyncMock())
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    msg = ReindexMsg(
        task_id=task.task_id, uri=ROOT, object_type="resource", mode="content_only",
        role="admin", content_retry_count=3, **OWNER,
    )
    outcome = await processor.on_dequeue({"data": msg.to_dict()})
    assert outcome.outcome.value == "failed"
    failed = await env.tracker.get(task.task_id, **OWNER)
    assert failed.status == TaskStatus.FAILED
    saved = failed.meta["content_only_checkpoint"]
    assert saved["after_id"] == "" and not saved["complete"]
    assert saved["error"]["record_id"] == rid("a")
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_bounded_keyset_pages_cover_more_than_limit(env):
    task = await make_task(env)
    records = [record(f"{i:04d}") for i in range(205)]
    env.store.filter.side_effect = [records[:100], records[100:200], records[200:], []]
    result = await run_task(env, task)
    assert result["scanned_records"] == result["content_updated_records"] == 205
    cursors = [c.kwargs["filter"].conds[-1] for c in env.store.filter.call_args_list[1:]]
    assert cursors == [Range("id", gt=rid(value)) for value in ("0099", "0199", "0204")]
    assert all(c.kwargs["limit"] == 100 for c in env.store.filter.call_args_list)


@pytest.mark.asyncio
async def test_cancellation_settles_inflight_cas_before_releasing_root_lock(env):
    task = await make_task(env)
    entered, settle = asyncio.Event(), asyncio.Event()

    async def pending_cas(*args, **kwargs):
        entered.set()
        await settle.wait()
        return True

    env.store.backfill_content_record.side_effect = pending_cas
    running = asyncio.create_task(run_task(env, task))
    await asyncio.wait_for(entered.wait(), 1)
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    env.agfs.pathlock_release.assert_not_awaited()
    settle.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    env.agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_inventory_failure_does_not_persist_false_completion(env):
    task = await make_task(env)
    env.store.filter.side_effect = ConnectionError("inventory unavailable")
    with pytest.raises(ConnectionError):
        await run_task(env, task)
    checkpoint = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert checkpoint["complete"] is False
    assert checkpoint["after_id"] == ""
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_router_preserves_authorization_and_force(monkeypatch):
    from openviking.server.routers import content
    from openviking_cli.exceptions import PermissionDeniedError

    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    service = SimpleNamespace(reindex=AsyncMock(return_value={"status": "accepted"}))
    monkeypatch.setattr(content, "get_service", lambda: service)
    uri = "viking://user/alice/memories"
    await content.reindex(
        content.ReindexRequest(uri=uri, mode="content_only", force=True, wait=False), ctx
    )
    service.reindex.assert_awaited_once_with(
        uri=uri, mode="content_only", force=True, wait=False, ctx=ctx
    )
    with pytest.raises(PermissionDeniedError):
        await content.reindex(
            content.ReindexRequest(uri="viking://user/bob/memories", mode="content_only"), ctx
        )
    assert service.reindex.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["a", "A" * 32, "é" * 32, "0" * 31, " " + "a" * 32])
async def test_keyset_rejects_noncanonical_collation_sensitive_ids(env, bad_id):
    task = await make_task(env)
    env.store.filter.side_effect = [[{**record("a"), "id": bad_id}]]
    with pytest.raises(RuntimeError, match="canonical"):
        await run_task(env, task)
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyset_rejects_cross_page_database_order_disagreement(env):
    task = await make_task(env)
    env.store.filter.side_effect = [[record("b")], [record("a")]]
    with pytest.raises(RuntimeError, match="keyset ordered"):
        await run_task(env, task)
    checkpoint = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert checkpoint["after_id"] == rid("b") and not checkpoint["complete"]
    assert env.store.backfill_content_record.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [[], [record("b", content="concurrent text")], [record("b")]])
@pytest.mark.parametrize("force", [False, True])
async def test_pending_conflict_refreshes_after_disk_restart(
    env, monkeypatch, tmp_path, refresh, force
):
    from tests.utils.mock_agfs import MockLocalAGFS

    tracker = TaskTracker(PersistentTaskStore(MockLocalAGFS(root_path=tmp_path)))
    monkeypatch.setattr(module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: tracker
    )
    task = await tracker.create("admin_reindex", resource_id=ROOT, **OWNER)
    env.store.backfill_content_record.side_effect = [True, False]
    env.store.get_strict.return_value = [record("b")]
    queue = SimpleNamespace(REINDEX="Reindex", enqueue=AsyncMock())
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    monkeypatch.setattr("openviking.storage.queuefs.reindex_processor.asyncio.sleep", AsyncMock())
    msg = ReindexMsg(
        task_id=task.task_id,
        uri=ROOT,
        object_type="resource",
        mode="content_only",
        role="admin",
        force=force,
        **OWNER,
    )
    processor = ReindexProcessor(env.fs)
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "first"})
    assert (await processor.on_dequeue({"data": msg.to_dict()})).outcome.value == "requeued"
    saved = (await tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert saved["after_id"] == rid("a")
    assert saved["error"]["type"] == "ContentBackfillConflictError"
    delivery_file = tmp_path / "delivery.json"
    delivery_file.write_text(json.dumps(queue.enqueue.call_args.args[1]), encoding="utf-8")
    fresh = TaskTracker(PersistentTaskStore(MockLocalAGFS(root_path=tmp_path)))
    monkeypatch.setattr(module, "get_task_tracker", lambda: fresh)
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", lambda: fresh
    )
    env.store.get_strict.return_value = refresh
    env.store.backfill_content_record.side_effect = None
    env.store.filter.side_effect = [[]]
    replay = ReindexProcessor(env.fs)
    replay._adopt_or_reacquire = AsyncMock(return_value={"lease": "second"})
    restored_delivery = json.loads(delivery_file.read_text(encoding="utf-8"))
    assert (await replay.on_dequeue({"data": restored_delivery})).outcome.value == "success"
    terminal = await fresh.get(task.task_id, **OWNER)
    assert terminal.status == TaskStatus.COMPLETED
    assert terminal.meta["content_only_checkpoint"]["after_id"] == rid("b")
    assert terminal.meta["content_only_checkpoint"]["error"] is None
    should_write = bool(refresh and (force or refresh[0]["content"] is None))
    assert terminal.result["content_skipped_records"] == (0 if should_write else 1)
    assert env.store.backfill_content_record.await_count == (3 if should_write else 2)
    if should_write and force:
        assert env.store.backfill_content_record.call_args.kwargs["repair"] is True


@pytest.mark.asyncio
async def test_repeated_source_failure_never_advances_pending_cursor(env):
    task = await make_task(env)
    env.fs.read_file.return_value = None
    first = await run_task(env, task)
    env.store.get_strict.return_value = [record("a")]
    second = await run_task(env, task)
    assert first["status"] == second["status"] == "retry_pending"
    saved = (await env.tracker.get(task.task_id, **OWNER)).meta["content_only_checkpoint"]
    assert saved["after_id"] == "" and saved["error"]["attempts"] == 2
    assert env.store.filter.await_count == 1
    env.store.backfill_content_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_enqueue_failure_keeps_task_replayable(env, monkeypatch):
    task = await make_task(env)
    env.fs.read_file.return_value = None
    queue = SimpleNamespace(
        REINDEX="Reindex", enqueue=AsyncMock(side_effect=ConnectionError("queue down"))
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    monkeypatch.setattr("openviking.storage.queuefs.reindex_processor.asyncio.sleep", AsyncMock())
    processor = ReindexProcessor(env.fs)
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "owned"})
    msg = ReindexMsg(
        task_id=task.task_id,
        uri=ROOT,
        object_type="resource",
        mode="content_only",
        role="admin",
        **OWNER,
    )
    with pytest.raises(ConnectionError):
        await processor.on_dequeue({"data": msg.to_dict()})
    pending = await env.tracker.get(task.task_id, **OWNER)
    assert pending.status == TaskStatus.RUNNING
    assert pending.stage == "content_only_retry"
    assert pending.meta["content_only_delivery_error"] == "queue down"
    assert pending.meta["content_only_checkpoint"]["after_id"] == ""
    assert pending.meta["content_only_checkpoint"]["error"]["type"] == "TypeError"


def test_content_only_rejects_tag_mutations():
    for options in ({"tags": ["x=y"], "tag_mode": "replace"}, {"tags": None, "tag_mode": "clear"}):
        with pytest.raises(InvalidArgumentError):
            ReindexExecutor._resolve_ingest_options(mode="content_only", **options)
