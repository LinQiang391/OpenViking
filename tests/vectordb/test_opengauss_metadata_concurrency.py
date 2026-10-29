"""Metadata adoption must synchronize with collection operations."""

import threading
from copy import deepcopy
from unittest.mock import Mock

from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection


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
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._lock = ObservedRLock()
    collection._conn = Mock()
    collection._dim = 0
    collection._distributed = False
    collection._meta = {"Fields": [{"FieldName": "id", "FieldType": "string"}]}
    collection._field_names = {"id"}
    before = deepcopy(collection._meta)
    fresh = deepcopy(before)
    fresh["_dim"] = 3
    fresh["Fields"].extend([
        {"FieldName": "tags", "FieldType": "list<string>"},
        {"FieldName": "changed_at", "FieldType": "date_time"},
        {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
    ])
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
            assert collection._lock.blocked.wait(timeout=2)
            assert not finished.is_set()
            assert collection._meta == before
            assert collection._dim == 0
            assert "tags" not in collection._field_names
    finally:
        if worker.ident is not None:
            worker.join(timeout=2)
    assert not worker.is_alive()
    assert finished.is_set() and not errors
    assert collection._meta == fresh
    assert collection._dim == 3
    assert {"tags", "changed_at", "sparse_vector", "vector"} <= collection._field_names
    assert collection._array_fields == {"tags"}
    assert collection._date_time_fields == {"changed_at"}
    collection._conn.cursor.assert_not_called()
