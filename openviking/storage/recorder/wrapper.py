# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Recorder wrapper for VikingFS and VikingDB.

Wraps existing storage backends to record IO operations.
"""

import time
from typing import Any, Dict, List, Optional, Union

from openviking.storage.recorder import IORecorder, IOType, get_recorder
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RecordingVikingFS:
    """
    Wrapper for VikingFS that records all operations.

    Usage:
        from openviking.storage.recorder import init_recorder
        from openviking.storage.recorder.wrapper import RecordingVikingFS

        init_recorder(enabled=True)
        fs = RecordingVikingFS(viking_fs)
        await fs.read(uri)  # This will be recorded
    """

    def __init__(self, viking_fs: Any, recorder: Optional[IORecorder] = None):
        """
        Initialize wrapper.

        Args:
            viking_fs: VikingFS instance to wrap
            recorder: IORecorder instance (uses global if None)
        """
        self._fs = viking_fs
        self._recorder = recorder or get_recorder()

    def _record(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Record an FS operation."""
        self._recorder.record_fs(
            operation=operation,
            request=request,
            response=response,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    async def read(self, uri: str, offset: int = 0, size: int = -1) -> bytes:
        """Read file with recording."""
        request = {"uri": uri, "offset": offset, "size": size}
        start_time = time.time()
        try:
            result = await self._fs.read(uri, offset, size)
            latency_ms = (time.time() - start_time) * 1000
            self._record("read", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("read", request, None, latency_ms, False, str(e))
            raise

    async def write(self, uri: str, data: Union[bytes, str]) -> str:
        """Write file with recording."""
        request = {"uri": uri}
        start_time = time.time()
        try:
            result = await self._fs.write(uri, data)
            latency_ms = (time.time() - start_time) * 1000
            self._record("write", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("write", request, None, latency_ms, False, str(e))
            raise

    async def ls(self, uri: str) -> List[Dict[str, Any]]:
        """List directory with recording."""
        request = {"uri": uri}
        start_time = time.time()
        try:
            result = await self._fs.ls(uri)
            latency_ms = (time.time() - start_time) * 1000
            self._record("ls", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("ls", request, None, latency_ms, False, str(e))
            raise

    async def stat(self, uri: str) -> Dict[str, Any]:
        """Get file info with recording."""
        request = {"uri": uri}
        start_time = time.time()
        try:
            result = await self._fs.stat(uri)
            latency_ms = (time.time() - start_time) * 1000
            self._record("stat", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("stat", request, None, latency_ms, False, str(e))
            raise

    async def mkdir(self, uri: str, mode: str = "755", exist_ok: bool = False) -> None:
        """Create directory with recording."""
        request = {"uri": uri, "mode": mode, "exist_ok": exist_ok}
        start_time = time.time()
        try:
            result = await self._fs.mkdir(uri, mode, exist_ok)
            latency_ms = (time.time() - start_time) * 1000
            self._record("mkdir", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("mkdir", request, None, latency_ms, False, str(e))
            raise

    async def rm(self, uri: str, recursive: bool = False) -> Dict[str, Any]:
        """Delete with recording."""
        request = {"uri": uri, "recursive": recursive}
        start_time = time.time()
        try:
            result = await self._fs.rm(uri, recursive)
            latency_ms = (time.time() - start_time) * 1000
            self._record("rm", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("rm", request, None, latency_ms, False, str(e))
            raise

    async def mv(self, old_uri: str, new_uri: str) -> Dict[str, Any]:
        """Move with recording."""
        request = {"old_uri": old_uri, "new_uri": new_uri}
        start_time = time.time()
        try:
            result = await self._fs.mv(old_uri, new_uri)
            latency_ms = (time.time() - start_time) * 1000
            self._record("mv", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("mv", request, None, latency_ms, False, str(e))
            raise

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Grep with recording."""
        request = {"uri": uri, "pattern": pattern, "case_insensitive": case_insensitive}
        start_time = time.time()
        try:
            result = await self._fs.grep(uri, pattern, case_insensitive)
            latency_ms = (time.time() - start_time) * 1000
            self._record("grep", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("grep", request, None, latency_ms, False, str(e))
            raise

    async def tree(
        self,
        uri: str = "viking://",
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
    ) -> List[Dict[str, Any]]:
        """Tree with recording."""
        request = {"uri": uri, "output": output, "abs_limit": abs_limit, "show_all_hidden": show_all_hidden}
        start_time = time.time()
        try:
            result = await self._fs.tree(uri, output, abs_limit, show_all_hidden)
            latency_ms = (time.time() - start_time) * 1000
            self._record("tree", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("tree", request, None, latency_ms, False, str(e))
            raise

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """Glob with recording."""
        request = {"pattern": pattern, "uri": uri}
        start_time = time.time()
        try:
            result = await self._fs.glob(pattern, uri)
            latency_ms = (time.time() - start_time) * 1000
            self._record("glob", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("glob", request, None, latency_ms, False, str(e))
            raise

    async def abstract(self, uri: str) -> str:
        """Get abstract with recording."""
        request = {"uri": uri}
        start_time = time.time()
        try:
            result = await self._fs.abstract(uri)
            latency_ms = (time.time() - start_time) * 1000
            self._record("abstract", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("abstract", request, None, latency_ms, False, str(e))
            raise

    async def overview(self, uri: str) -> str:
        """Get overview with recording."""
        request = {"uri": uri}
        start_time = time.time()
        try:
            result = await self._fs.overview(uri)
            latency_ms = (time.time() - start_time) * 1000
            self._record("overview", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("overview", request, None, latency_ms, False, str(e))
            raise

    def __getattr__(self, name: str) -> Any:
        """Pass through any other attributes to the wrapped fs."""
        return getattr(self._fs, name)


class RecordingVikingDB:
    """
    Wrapper for VikingDBInterface that records all operations.

    Usage:
        from openviking.storage.recorder import init_recorder
        from openviking.storage.recorder.wrapper import RecordingVikingDB

        init_recorder(enabled=True)
        db = RecordingVikingDB(vector_store)
        await db.search(...)  # This will be recorded
    """

    def __init__(self, viking_db: Any, recorder: Optional[IORecorder] = None):
        """
        Initialize wrapper.

        Args:
            viking_db: VikingDBInterface instance to wrap
            recorder: IORecorder instance (uses global if None)
        """
        self._db = viking_db
        self._recorder = recorder or get_recorder()

    def _record(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Record a VikingDB operation."""
        self._recorder.record_vikingdb(
            operation=operation,
            request=request,
            response=response,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )

    async def insert(self, collection: str, data: Dict[str, Any]) -> str:
        """Insert with recording."""
        request = {"collection": collection, "data": data}
        start_time = time.time()
        try:
            result = await self._db.insert(collection, data)
            latency_ms = (time.time() - start_time) * 1000
            self._record("insert", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("insert", request, None, latency_ms, False, str(e))
            raise

    async def update(self, collection: str, id: str, data: Dict[str, Any]) -> bool:
        """Update with recording."""
        request = {"collection": collection, "id": id, "data": data}
        start_time = time.time()
        try:
            result = await self._db.update(collection, id, data)
            latency_ms = (time.time() - start_time) * 1000
            self._record("update", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("update", request, None, latency_ms, False, str(e))
            raise

    async def upsert(self, collection: str, data: Dict[str, Any]) -> str:
        """Upsert with recording."""
        request = {"collection": collection, "data": data}
        start_time = time.time()
        try:
            result = await self._db.upsert(collection, data)
            latency_ms = (time.time() - start_time) * 1000
            self._record("upsert", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("upsert", request, None, latency_ms, False, str(e))
            raise

    async def delete(self, collection: str, ids: List[str]) -> int:
        """Delete with recording."""
        request = {"collection": collection, "ids": ids}
        start_time = time.time()
        try:
            result = await self._db.delete(collection, ids)
            latency_ms = (time.time() - start_time) * 1000
            self._record("delete", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("delete", request, None, latency_ms, False, str(e))
            raise

    async def get(self, collection: str, ids: List[str]) -> List[Dict[str, Any]]:
        """Get with recording."""
        request = {"collection": collection, "ids": ids}
        start_time = time.time()
        try:
            result = await self._db.get(collection, ids)
            latency_ms = (time.time() - start_time) * 1000
            self._record("get", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("get", request, None, latency_ms, False, str(e))
            raise

    async def exists(self, collection: str, id: str) -> bool:
        """Exists with recording."""
        request = {"collection": collection, "id": id}
        start_time = time.time()
        try:
            result = await self._db.exists(collection, id)
            latency_ms = (time.time() - start_time) * 1000
            self._record("exists", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("exists", request, None, latency_ms, False, str(e))
            raise

    async def search(
        self,
        collection: str,
        vector: List[float],
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search with recording."""
        request = {"collection": collection, "vector": vector, "top_k": top_k, "filter": filter}
        start_time = time.time()
        try:
            result = await self._db.search(collection, vector, top_k, filter)
            latency_ms = (time.time() - start_time) * 1000
            self._record("search", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("search", request, None, latency_ms, False, str(e))
            raise

    async def filter(
        self,
        collection: str,
        filter: Dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Filter with recording."""
        request = {"collection": collection, "filter": filter, "limit": limit, "offset": offset}
        start_time = time.time()
        try:
            result = await self._db.filter(collection, filter, limit, offset)
            latency_ms = (time.time() - start_time) * 1000
            self._record("filter", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("filter", request, None, latency_ms, False, str(e))
            raise

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        """Create collection with recording."""
        request = {"name": name, "schema": schema}
        start_time = time.time()
        try:
            result = await self._db.create_collection(name, schema)
            latency_ms = (time.time() - start_time) * 1000
            self._record("create_collection", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("create_collection", request, None, latency_ms, False, str(e))
            raise

    async def drop_collection(self, name: str) -> bool:
        """Drop collection with recording."""
        request = {"name": name}
        start_time = time.time()
        try:
            result = await self._db.drop_collection(name)
            latency_ms = (time.time() - start_time) * 1000
            self._record("drop_collection", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("drop_collection", request, None, latency_ms, False, str(e))
            raise

    async def collection_exists(self, name: str) -> bool:
        """Check collection exists with recording."""
        request = {"name": name}
        start_time = time.time()
        try:
            result = await self._db.collection_exists(name)
            latency_ms = (time.time() - start_time) * 1000
            self._record("collection_exists", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("collection_exists", request, None, latency_ms, False, str(e))
            raise

    async def list_collections(self) -> List[str]:
        """List collections with recording."""
        request = {}
        start_time = time.time()
        try:
            result = await self._db.list_collections()
            latency_ms = (time.time() - start_time) * 1000
            self._record("list_collections", request, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            self._record("list_collections", request, None, latency_ms, False, str(e))
            raise

    def __getattr__(self, name: str) -> Any:
        """Pass through any other attributes to the wrapped db."""
        return getattr(self._db, name)
