# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Connection lifecycle tests use only an in-memory pool, never a database DSN."""

import threading
import time
from unittest.mock import Mock

import pytest
from psycopg2.pool import PoolError

from openviking.storage.vectordb_adapters.opengauss.connection import _PooledConnectionProxy


def _connection():
    connection = Mock()
    connection.closed = False
    connection.cursor.side_effect = lambda *args, **kwargs: Mock()
    return connection


class _Pool:
    def __init__(self, maxconn=1):
        self.maxconn = maxconn
        self.available = []
        self.used = set()
        self.created = []
        self.returned = []
        self.closed = False
        self.close_calls = 0
        self.get_calls = 0
        self.peak_used = 0
        self.lock = threading.Lock()

    def getconn(self):
        with self.lock:
            self.get_calls += 1
            if self.closed:
                raise PoolError("connection pool is closed")
            if self.available:
                connection = self.available.pop()
            elif len(self.used) < self.maxconn:
                connection = _connection()
                self.created.append(connection)
            else:
                raise PoolError("connection pool exhausted")
            self.used.add(connection)
            self.peak_used = max(self.peak_used, len(self.used))
            return connection

    def putconn(self, connection, close=False):
        with self.lock:
            if self.closed:
                raise PoolError("connection pool is closed")
            self.used.remove(connection)
            self.returned.append((connection, close))
            if close:
                connection.closed = True
            else:
                self.available.append(connection)

    def closeall(self):
        with self.lock:
            self.close_calls += 1
            self.closed = True
            for connection in self.created:
                connection.closed = True
            self.used.clear()
            self.available.clear()


def _start(action):
    errors = []
    done = threading.Event()

    def run():
        try:
            action()
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, errors


def _finish(worker):
    thread, done, errors = worker
    assert done.wait(3), "pool worker did not terminate"
    thread.join(timeout=1)
    assert not thread.is_alive()
    return errors


def _observe_wait(proxy, monkeypatch):
    waiting = threading.Event()
    original = proxy._condition.wait

    def wait(timeout=None):
        waiting.set()
        return original(timeout)

    monkeypatch.setattr(proxy._condition, "wait", wait)
    return waiting


def test_exhausted_pool_waits_until_last_cursor_returns_connection(monkeypatch):
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool, timeout=1)
    first = proxy.cursor()
    second = proxy.cursor()
    waiting = _observe_wait(proxy, monkeypatch)
    acquired = threading.Event()

    def borrow():
        cursor = proxy.cursor()
        acquired.set()
        cursor.close()

    worker = _start(borrow)
    try:
        assert waiting.wait(1)
        first.close()
        assert not acquired.is_set()
        assert pool.returned == []
        second.close()
        assert _finish(worker) == []
        assert acquired.is_set()
        assert len(pool.created) == 1
        assert pool.peak_used == 1
        assert len(pool.returned) == 2
    finally:
        first.close()
        second.close()
        proxy.close()
        _finish(worker)


def test_pool_timeout_is_bounded_and_does_not_busy_spin(monkeypatch):
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool, timeout=0.08)
    cursor = proxy.cursor()
    waiting = _observe_wait(proxy, monkeypatch)
    started = time.monotonic()
    worker = _start(proxy.cursor)
    try:
        assert waiting.wait(1)
        errors = _finish(worker)
        elapsed = time.monotonic() - started
        assert len(errors) == 1
        assert isinstance(errors[0], TimeoutError)
        assert isinstance(errors[0].__cause__, PoolError)
        assert 0.06 <= elapsed < 2
        assert pool.get_calls <= 5
        assert pool.returned == []
    finally:
        cursor.close()
        proxy.close()


def test_zero_timeout_still_allows_immediate_checkout():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool, timeout=0)
    cursor = proxy.cursor()
    worker = _start(proxy.cursor)
    try:
        errors = _finish(worker)
        assert len(errors) == 1
        assert isinstance(errors[0], TimeoutError)
        assert pool.get_calls == 2
    finally:
        cursor.close()
        proxy.close()


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), None, True, "1"])
def test_invalid_timeouts_are_rejected(timeout):
    with pytest.raises(ValueError, match="timeout"):
        _PooledConnectionProxy(_Pool(), timeout=timeout)


def test_default_timeout_preserves_constructor_compatibility():
    proxy = _PooledConnectionProxy(Mock())
    assert proxy._timeout == 10.0
    proxy.close()


def test_close_wakes_all_waiters_and_late_release_does_not_putconn(monkeypatch):
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool, timeout=10)
    cursor = proxy.cursor()
    original_wait = proxy._condition.wait
    wait_count = 0
    all_waiting = threading.Event()

    def wait(timeout=None):
        nonlocal wait_count
        wait_count += 1
        if wait_count >= 4:
            all_waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(proxy._condition, "wait", wait)
    workers = [_start(proxy.cursor) for _ in range(4)]
    try:
        assert all_waiting.wait(1)
        proxy.close()
        for worker in workers:
            errors = _finish(worker)
            assert len(errors) == 1
            assert isinstance(errors[0], RuntimeError)
            assert "closed" in str(errors[0])
        cursor.close()
        proxy.close()
        assert pool.close_calls == 1
        assert pool.returned == []
        assert proxy._local.connection is None
        assert proxy._local.cursor_count == 0
        with pytest.raises(RuntimeError, match="closed"):
            proxy.cursor()
    finally:
        proxy.close()
        for worker in workers:
            _finish(worker)


def test_worker_cursor_can_close_after_another_thread_closes_pool():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    acquired = threading.Event()
    release = threading.Event()
    local_state = []

    def borrow():
        cursor = proxy.cursor()
        acquired.set()
        assert release.wait(2)
        cursor.close()
        local_state.append((proxy._local.connection, proxy._local.cursor_count))

    worker = _start(borrow)
    try:
        assert acquired.wait(1)
        proxy.close()
    finally:
        release.set()
    assert _finish(worker) == []
    assert local_state == [(None, 0)]
    assert pool.returned == []
    assert pool.close_calls == 1


def test_closeall_failure_still_closes_proxy_and_wakes_waiter(monkeypatch):
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool, timeout=10)
    cursor = proxy.cursor()
    waiting = _observe_wait(proxy, monkeypatch)
    monkeypatch.setattr(pool, "closeall", Mock(side_effect=RuntimeError("shutdown failed")))
    worker = _start(proxy.cursor)
    try:
        assert waiting.wait(1)
        with pytest.raises(RuntimeError, match="shutdown failed"):
            proxy.close()
        errors = _finish(worker)
        assert len(errors) == 1
        assert "closed" in str(errors[0])
        cursor.close()
        proxy.close()
        pool.closeall.assert_called_once_with()
        assert pool.returned == []
    finally:
        proxy.close()
        _finish(worker)


def test_closed_idle_connection_is_discarded_before_replacement():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    connection = proxy._checkout()
    connection.closed = True
    replacement = proxy._checkout()
    assert replacement is not connection
    assert pool.returned == [(connection, True)]
    assert replacement.autocommit is False
    proxy.release(replacement)
    proxy.close()


def test_broken_active_transaction_is_not_replaced_or_committed():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    cursor = proxy.cursor()
    connection = cursor._connection
    cursor.execute("INSERT INTO example VALUES (1)")
    connection.closed = True
    with pytest.raises(RuntimeError, match="transaction connection is broken"):
        proxy.commit()
    with pytest.raises(RuntimeError, match="transaction connection is broken"):
        proxy.cursor()
    assert len(pool.created) == 1
    connection.commit.assert_not_called()
    cursor._cursor.execute.assert_called_once_with("INSERT INTO example VALUES (1)")
    cursor.close()
    assert pool.returned == [(connection, True)]
    replacement_cursor = proxy.cursor()
    assert replacement_cursor._connection is not connection
    replacement_cursor.close()
    proxy.close()


def test_commit_failure_is_not_retried_and_connection_is_discarded():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    cursor = proxy.cursor()
    connection = cursor._connection
    connection.commit.side_effect = RuntimeError("commit outcome unknown")
    cursor.execute("INSERT INTO example VALUES (1)")
    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        proxy.commit()
    with pytest.raises(RuntimeError, match="transaction connection is broken"):
        proxy.commit()
    cursor.close()
    connection.commit.assert_called_once_with()
    cursor._cursor.execute.assert_called_once_with("INSERT INTO example VALUES (1)")
    assert len(pool.created) == 1
    assert pool.returned == [(connection, True)]
    proxy.close()


@pytest.mark.parametrize("operation", ["commit", "rollback"])
def test_transaction_without_cursors_does_not_leak_capacity(operation):
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    connection = proxy._checkout()
    getattr(proxy, operation)()
    assert proxy._local.connection is None
    assert proxy._local.cursor_count == 0
    assert pool.returned == [(connection, False)]
    proxy.close()


def test_rollback_failure_is_discarded_after_all_cursors_close():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    first = proxy.cursor()
    second = proxy.cursor()
    connection = first._connection
    connection.rollback.side_effect = RuntimeError("rollback failed")
    with pytest.raises(RuntimeError, match="rollback failed"):
        proxy.rollback()
    first.close()
    assert pool.returned == []
    second.close()
    assert pool.returned == [(connection, True)]
    connection.rollback.assert_called_once_with()
    proxy.close()


def test_cleanup_rollback_failure_discards_connection_and_wakes_waiter(monkeypatch):
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool, timeout=1)
    cursor = proxy.cursor()
    connection = cursor._connection
    connection.rollback.side_effect = RuntimeError("rollback failed")
    waiting = _observe_wait(proxy, monkeypatch)

    def borrow():
        other = proxy.cursor()
        assert other._connection is not connection
        other.close()

    worker = _start(borrow)
    try:
        assert waiting.wait(1)
        cursor.close()
        assert _finish(worker) == []
        assert pool.returned[0] == (connection, True)
    finally:
        cursor.close()
        proxy.close()
        _finish(worker)


def test_failed_nested_cursor_creation_preserves_existing_cursor_count():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    first = proxy.cursor()
    connection = first._connection
    connection.cursor.side_effect = RuntimeError("cursor failed")
    with pytest.raises(RuntimeError, match="cursor failed"):
        proxy.cursor()
    assert proxy._local.cursor_count == 1
    assert pool.returned == []
    first.close()
    first.close()
    assert proxy._local.cursor_count == 0
    assert pool.returned == [(connection, False)]
    proxy.close()


def test_cursor_close_failure_is_counted_once_and_discards_connection():
    pool = _Pool()
    proxy = _PooledConnectionProxy(pool)
    first = proxy.cursor()
    second = proxy.cursor()
    connection = first._connection
    first._cursor.close.side_effect = RuntimeError("cursor close failed")
    with pytest.raises(RuntimeError, match="cursor close failed"):
        first.close()
    first.close()
    assert proxy._local.cursor_count == 1
    assert pool.returned == []
    second.close()
    assert pool.returned == [(connection, True)]
    proxy.close()


def test_checkout_initialization_failure_returns_bad_connection_and_notifies():
    pool = Mock()
    connection = Mock()
    type(connection).autocommit = property(fset=Mock(side_effect=RuntimeError("init failed")))
    pool.getconn.return_value = connection
    proxy = _PooledConnectionProxy(pool)
    with pytest.raises(RuntimeError, match="init failed"):
        proxy.cursor()
    pool.putconn.assert_called_once_with(connection, close=True)
    assert getattr(proxy._local, "connection", None) is None
    proxy.close()


@pytest.mark.parametrize(
    "error", [RuntimeError("network failed"), PoolError("connection pool is closed")]
)
def test_non_exhaustion_errors_propagate_without_retry(error):
    pool = Mock()
    pool.getconn.side_effect = error
    proxy = _PooledConnectionProxy(pool)
    with pytest.raises(type(error), match=str(error)) as caught:
        proxy.cursor()
    assert caught.value is error
    pool.getconn.assert_called_once_with()
    proxy.close()


def test_direct_pool_return_is_found_by_bounded_condition_recheck(monkeypatch):
    pool = _Pool()
    connection = pool.getconn()
    proxy = _PooledConnectionProxy(pool, timeout=1)
    waiting = _observe_wait(proxy, monkeypatch)

    def borrow():
        cursor = proxy.cursor()
        assert cursor._connection is connection
        cursor.close()

    worker = _start(borrow)
    try:
        assert waiting.wait(1)
        pool.putconn(connection)
        assert _finish(worker) == []
    finally:
        proxy.close()
        _finish(worker)


def test_parallel_transactions_share_capacity_without_exhaustion_errors():
    pool = _Pool(maxconn=2)
    proxy = _PooledConnectionProxy(pool, timeout=2)
    start = threading.Barrier(9, timeout=3)
    completed = []

    def transact():
        start.wait()
        for _ in range(15):
            first = proxy.cursor()
            second = proxy.cursor()
            try:
                assert first._connection is second._connection
                first.execute("INSERT INTO example VALUES (1)")
                proxy.commit()
            finally:
                first.close()
                second.close()
        completed.append(threading.get_ident())

    workers = [_start(transact) for _ in range(8)]
    try:
        start.wait()
        for worker in workers:
            assert _finish(worker) == []
        assert len(completed) == 8
        assert pool.peak_used <= 2
        assert len(pool.created) <= 2
        assert len(pool.returned) == 120
        assert not pool.used
    finally:
        proxy.close()
        for worker in workers:
            _finish(worker)
