# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Playback module for IORecorder.

Replays recorded IO operations and compares performance across different backends.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.storage.recorder import IORecord, IOType
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PlaybackResult:
    """
    Result of a single playback operation.

    Attributes:
        record: Original IO record
        playback_latency_ms: Latency during playback
        playback_success: Whether playback succeeded
        playback_error: Error message if failed
        response_match: Whether response matches original
    """

    record: IORecord
    playback_latency_ms: float = 0.0
    playback_success: bool = True
    playback_error: Optional[str] = None
    response_match: Optional[bool] = None


@dataclass
class PlaybackStats:
    """
    Statistics for playback session.

    Attributes:
        total_records: Total number of records played
        success_count: Number of successful operations
        error_count: Number of failed operations
        total_original_latency_ms: Total original latency
        total_playback_latency_ms: Total playback latency
        fs_stats: Statistics for FS operations
        vikingdb_stats: Statistics for VikingDB operations
    """

    total_records: int = 0
    success_count: int = 0
    error_count: int = 0
    total_original_latency_ms: float = 0.0
    total_playback_latency_ms: float = 0.0
    fs_stats: Dict[str, Dict[str, Any]] = None
    vikingdb_stats: Dict[str, Dict[str, Any]] = None

    def __post_init__(self):
        if self.fs_stats is None:
            self.fs_stats = {}
        if self.vikingdb_stats is None:
            self.vikingdb_stats = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_records": self.total_records,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "total_original_latency_ms": self.total_original_latency_ms,
            "total_playback_latency_ms": self.total_playback_latency_ms,
            "speedup_ratio": (
                self.total_original_latency_ms / self.total_playback_latency_ms
                if self.total_playback_latency_ms > 0
                else 0
            ),
            "fs_stats": self.fs_stats,
            "vikingdb_stats": self.vikingdb_stats,
        }


class IOPlayback:
    """
    Playback recorded IO operations.

    Replays recorded operations against a target backend and measures performance.

    Usage:
        playback = IOPlayback(config_file="./ov.conf")
        stats = await playback.play(record_file="./records/io_recorder_20260214.jsonl")
        print(stats.to_dict())
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        compare_response: bool = False,
        fail_fast: bool = False,
        enable_fs: bool = True,
        enable_vikingdb: bool = True,
    ):
        """
        Initialize IOPlayback.

        Args:
            config_file: Path to OpenViking config file (ov.conf)
            compare_response: Whether to compare playback response with original
            fail_fast: Stop on first error
            enable_fs: Whether to play FS operations
            enable_vikingdb: Whether to play VikingDB operations
        """
        self.config_file = config_file
        self.compare_response = compare_response
        self.fail_fast = fail_fast
        self.enable_fs = enable_fs
        self.enable_vikingdb = enable_vikingdb
        self._viking_fs = None
        self._vector_store = None

    def _path_to_uri(self, path: str) -> str:
        """Convert AGFS path to VikingFS URI."""
        if path.startswith("viking://"):
            return path
        if path.startswith("/local/"):
            return "viking://" + path[7:]
        if path.startswith("/"):
            return "viking://" + path[1:]
        return f"viking://{path}"

    def _init_backends(self) -> None:
        """Initialize backend clients from config."""
        if self.config_file:
            import os

            os.environ["OPENVIKING_CONFIG_FILE"] = self.config_file

        from openviking.agfs_manager import AGFSManager
        from openviking.storage.viking_fs import init_viking_fs
        from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
        from openviking_cli.utils.config import get_openviking_config
        from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

        config = get_openviking_config()

        agfs_url = config.storage.agfs.url
        agfs_manager = None

        if config.storage.agfs.backend == "local":
            agfs_manager = AGFSManager(config=config.storage.agfs)
            agfs_manager.start()
            agfs_url = agfs_manager.url
            logger.info(f"[IOPlayback] Started AGFS at {agfs_url}")
        elif config.storage.agfs.backend in ["s3", "memory"]:
            agfs_manager = AGFSManager(config=config.storage.agfs)
            agfs_manager.start()
            agfs_url = agfs_manager.url
            logger.info(
                f"[IOPlayback] Started AGFS with {config.storage.agfs.backend} backend at {agfs_url}"
            )
        elif not agfs_url:
            agfs_url = "http://localhost:8080"
            logger.warning(f"[IOPlayback] No AGFS URL configured, using default: {agfs_url}")

        vector_store = None
        if self.enable_vikingdb:
            vectordb_config = config.storage.vectordb
            backend_config = VectorDBBackendConfig(
                backend=vectordb_config.backend or "local",
                path=vectordb_config.path or "./data/vectordb",
                url=vectordb_config.url,
                dimension=config.embedding.dimension,
            )
            if vectordb_config.volcengine:
                backend_config.volcengine = vectordb_config.volcengine
            vector_store = VikingVectorIndexBackend(config=backend_config)

        if self.enable_fs:
            self._viking_fs = init_viking_fs(
                agfs_url=agfs_url,
                vector_store=vector_store,
            )
        self._vector_store = vector_store

        logger.info(
            f"[IOPlayback] Initialized with config: {self.config_file}, "
            f"fs={self.enable_fs}, vikingdb={self.enable_vikingdb}"
        )

    async def _play_fs_operation(self, record: IORecord) -> PlaybackResult:
        """Play a single FS operation."""
        result = PlaybackResult(record=record)
        start_time = time.time()

        try:
            operation = record.operation
            request = record.request

            args = request.get("args", [])
            kwargs = request.get("kwargs", {})

            if operation == "read":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
                uri = self._path_to_uri(path)
                await self._viking_fs.read(
                    uri=uri,
                    offset=args[1] if len(args) > 1 else kwargs.get("offset", 0),
                    size=args[2] if len(args) > 2 else kwargs.get("size", -1),
                )
            elif operation == "write":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
                uri = self._path_to_uri(path)
                data = args[1] if len(args) > 1 else kwargs.get("data", b"")
                if isinstance(data, dict) and "__bytes__" in data:
                    data = data["__bytes__"].encode("utf-8")
                await self._viking_fs.write(
                    uri=uri,
                    data=data,
                )
            elif operation == "ls":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", "/"))
                uri = self._path_to_uri(path)
                await self._viking_fs.ls(uri=uri)
            elif operation == "stat":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
                uri = self._path_to_uri(path)
                await self._viking_fs.stat(uri=uri)
            elif operation == "mkdir":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
                uri = self._path_to_uri(path)
                await self._viking_fs.mkdir(
                    uri=uri,
                    mode=args[1] if len(args) > 1 else kwargs.get("mode", "755"),
                )
            elif operation == "rm":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
                uri = self._path_to_uri(path)
                await self._viking_fs.rm(
                    uri=uri,
                    recursive=args[1] if len(args) > 1 else kwargs.get("recursive", False),
                )
            elif operation == "mv":
                old_path = args[0] if args else kwargs.get("old_path", kwargs.get("old_uri", ""))
                new_path = (
                    args[1] if len(args) > 1 else kwargs.get("new_path", kwargs.get("new_uri", ""))
                )
                await self._viking_fs.mv(
                    old_uri=self._path_to_uri(old_path),
                    new_uri=self._path_to_uri(new_path),
                )
            elif operation == "grep":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", ""))
                uri = self._path_to_uri(path)
                await self._viking_fs.grep(
                    uri=uri,
                    pattern=args[1] if len(args) > 1 else kwargs.get("pattern", ""),
                )
            elif operation == "tree":
                path = args[0] if args else kwargs.get("path", kwargs.get("uri", "/"))
                uri = self._path_to_uri(path)
                await self._viking_fs.tree(uri=uri)
            elif operation == "glob":
                await self._viking_fs.glob(
                    pattern=args[0] if args else kwargs.get("pattern", "*"),
                )
            else:
                raise ValueError(f"Unknown FS operation: {operation}")

            result.playback_latency_ms = (time.time() - start_time) * 1000
            result.playback_success = True

        except Exception as e:
            result.playback_latency_ms = (time.time() - start_time) * 1000
            playback_error = str(e)

            if record.error and self._errors_match(playback_error, record.error):
                result.playback_success = True
                result.playback_error = f"Matched original error: {playback_error}"
            else:
                result.playback_success = False
                result.playback_error = playback_error
                logger.error(f"[IOPlayback] FS {operation} failed: {e}")

        return result

    def _errors_match(self, playback_error: str, record_error: str) -> bool:
        """Check if playback error matches original record error."""
        playback_lower = playback_error.lower()
        record_lower = record_error.lower()

        if playback_lower == record_lower:
            return True

        error_type_patterns = [
            (
                "no such file",
                ["no such file", "not found", "does not exist", "no such file or directory"],
            ),
            ("not a directory", ["not a directory", "not directory"]),
            ("is a directory", ["is a directory", "is directory"]),
            ("permission denied", ["permission denied", "access denied"]),
            ("already exists", ["already exists", "file exists", "directory already exists"]),
            ("directory not empty", ["directory not empty", "not empty"]),
            ("connection refused", ["connection refused", "server not running"]),
            ("timeout", ["timeout", "timed out"]),
            ("failed to stat", ["failed to stat", "stat failed"]),
        ]

        for _error_type, patterns in error_type_patterns:
            playback_match = any(p in playback_lower for p in patterns)
            record_match = any(p in record_lower for p in patterns)
            if playback_match and record_match:
                return True

        return False

    async def _play_vikingdb_operation(self, record: IORecord) -> PlaybackResult:
        """Play a single VikingDB operation."""
        result = PlaybackResult(record=record)
        start_time = time.time()

        try:
            operation = record.operation
            request = record.request

            args = request.get("args", [])
            kwargs = request.get("kwargs", {})

            if operation == "insert":
                await self._vector_store.insert(*args, **kwargs)
            elif operation == "update":
                await self._vector_store.update(*args, **kwargs)
            elif operation == "upsert":
                await self._vector_store.upsert(*args, **kwargs)
            elif operation == "delete":
                await self._vector_store.delete(*args, **kwargs)
            elif operation == "get":
                await self._vector_store.get(*args, **kwargs)
            elif operation == "exists":
                await self._vector_store.exists(*args, **kwargs)
            elif operation == "search":
                await self._vector_store.search(*args, **kwargs)
            elif operation == "filter":
                await self._vector_store.filter(*args, **kwargs)
            elif operation == "create_collection":
                await self._vector_store.create_collection(*args, **kwargs)
            elif operation == "drop_collection":
                await self._vector_store.drop_collection(*args, **kwargs)
            elif operation == "collection_exists":
                await self._vector_store.collection_exists(*args, **kwargs)
            elif operation == "list_collections":
                await self._vector_store.list_collections(*args, **kwargs)
            else:
                raise ValueError(f"Unknown VikingDB operation: {operation}")

            result.playback_latency_ms = (time.time() - start_time) * 1000
            result.playback_success = True

        except Exception as e:
            result.playback_latency_ms = (time.time() - start_time) * 1000
            playback_error = str(e)

            if record.error and self._errors_match(playback_error, record.error):
                result.playback_success = True
                result.playback_error = f"Matched original error: {playback_error}"
            else:
                result.playback_success = False
                result.playback_error = playback_error
                logger.error(f"[IOPlayback] VikingDB {operation} failed: {e}")

        return result

    async def play_record(self, record: IORecord) -> PlaybackResult:
        """Play a single record."""
        if record.io_type == IOType.FS.value:
            if not self.enable_fs:
                return PlaybackResult(record=record, playback_success=True)
            return await self._play_fs_operation(record)
        else:
            if not self.enable_vikingdb:
                return PlaybackResult(record=record, playback_success=True)
            return await self._play_vikingdb_operation(record)

    async def play(
        self,
        record_file: str,
        limit: Optional[int] = None,
        offset: int = 0,
        io_type: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> PlaybackStats:
        """
        Play all records from a record file.

        Args:
            record_file: Path to the record JSONL file
            limit: Maximum number of records to play
            offset: Number of records to skip
            io_type: Filter by IO type (fs or vikingdb)
            operation: Filter by operation name

        Returns:
            PlaybackStats with playback results
        """
        need_fs = self.enable_fs and (io_type is None or io_type == "fs")
        need_vikingdb = self.enable_vikingdb and (io_type is None or io_type == "vikingdb")

        if need_fs or need_vikingdb:
            self._init_backends()

        records = []
        with open(record_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(IORecord.from_dict(json.loads(line)))

        filtered_records = []
        for r in records:
            if io_type and r.io_type != io_type:
                continue
            if operation and r.operation != operation:
                continue
            if r.io_type == IOType.FS.value and not self.enable_fs:
                continue
            if r.io_type == IOType.VIKINGDB.value and not self.enable_vikingdb:
                continue
            filtered_records.append(r)

        records = filtered_records[offset:]
        if limit:
            records = records[:limit]

        stats = PlaybackStats(total_records=len(records))
        logger.info(f"[IOPlayback] Playing {len(records)} records from {record_file}")

        for i, record in enumerate(records):
            result = await self.play_record(record)

            stats.total_original_latency_ms += record.latency_ms
            stats.total_playback_latency_ms += result.playback_latency_ms

            if result.playback_success:
                stats.success_count += 1
            else:
                stats.error_count += 1

            op_key = f"{record.io_type}.{record.operation}"
            if record.io_type == IOType.FS.value:
                if op_key not in stats.fs_stats:
                    stats.fs_stats[op_key] = {
                        "count": 0,
                        "total_original_latency_ms": 0.0,
                        "total_playback_latency_ms": 0.0,
                    }
                stats.fs_stats[op_key]["count"] += 1
                stats.fs_stats[op_key]["total_original_latency_ms"] += record.latency_ms
                stats.fs_stats[op_key]["total_playback_latency_ms"] += result.playback_latency_ms
            else:
                if op_key not in stats.vikingdb_stats:
                    stats.vikingdb_stats[op_key] = {
                        "count": 0,
                        "total_original_latency_ms": 0.0,
                        "total_playback_latency_ms": 0.0,
                    }
                stats.vikingdb_stats[op_key]["count"] += 1
                stats.vikingdb_stats[op_key]["total_original_latency_ms"] += record.latency_ms
                stats.vikingdb_stats[op_key]["total_playback_latency_ms"] += (
                    result.playback_latency_ms
                )

            if (i + 1) % 100 == 0:
                logger.info(f"[IOPlayback] Progress: {i + 1}/{len(records)}")

            if self.fail_fast and not result.playback_success:
                logger.error(f"[IOPlayback] Stopping due to error at record {i + 1}")
                break

        logger.info(
            f"[IOPlayback] Completed: {stats.success_count}/{stats.total_records} successful"
        )
        return stats

    def play_sync(self, **kwargs) -> PlaybackStats:
        """Synchronous wrapper for play method."""
        return asyncio.run(self.play(**kwargs))


def load_records(record_file: str) -> List[IORecord]:
    """Load records from a JSONL file."""
    records = []
    with open(record_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(IORecord.from_dict(json.loads(line)))
    return records


def get_record_stats(record_file: str) -> Dict[str, Any]:
    """Get statistics from a record file without playback."""
    records = load_records(record_file)

    stats = {
        "file": record_file,
        "total_records": len(records),
        "fs_count": 0,
        "vikingdb_count": 0,
        "total_latency_ms": 0.0,
        "operations": {},
        "time_range": {
            "start": None,
            "end": None,
        },
    }

    for record in records:
        stats["total_latency_ms"] += record.latency_ms

        if record.io_type == IOType.FS.value:
            stats["fs_count"] += 1
        else:
            stats["vikingdb_count"] += 1

        op_key = f"{record.io_type}.{record.operation}"
        if op_key not in stats["operations"]:
            stats["operations"][op_key] = {"count": 0, "total_latency_ms": 0.0}
        stats["operations"][op_key]["count"] += 1
        stats["operations"][op_key]["total_latency_ms"] += record.latency_ms

        if stats["time_range"]["start"] is None:
            stats["time_range"]["start"] = record.timestamp
        stats["time_range"]["end"] = record.timestamp

    return stats
