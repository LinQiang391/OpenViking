import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection


def test_dense_read_transactions_overlap_on_one_collection():
    collection = OpenGaussCollection.__new__(OpenGaussCollection)
    collection._lock = threading.RLock()
    collection._conn = Mock()
    collection._apply_search_params_on_cursor = Mock()
    barrier = threading.Barrier(2)
    cursors = []

    def make_cursor():
        cursor = Mock()
        cursor.description = [("id",)]
        cursor.fetchall.return_value = [(threading.get_ident(),)]
        cursor.execute.side_effect = lambda sql, params: barrier.wait(timeout=3)
        cursors.append(cursor)
        return cursor

    collection._cursor = make_cursor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: collection._query_rows("SELECT id", [], "default"), range(2)))
    assert len({result[1][0][0] for result in results}) == 2
    assert collection._conn.commit.call_count == 2
    assert len(cursors) == 2
    assert all(cursor.close.call_count == 1 for cursor in cursors)
