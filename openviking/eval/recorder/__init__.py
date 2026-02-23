# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
IO Recorder for OpenViking evaluation.

Records IO operations (fs, vikingdb) during evaluation for later playback.
"""

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from enum import Enum

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_RECORDS_DIR = "./records"


class IOType(Enum):
    """IO operation type."""
    FS = "fs"
    VIKINGDB = "vikingdb"


class FSOperation(Enum):
    """File system operations."""
    READ = "read"
    WRITE = "write"
    LS = "ls"
    STAT = "stat"
    MKDIR = "mkdir"
    RM = "rm"
    MV = "mv"
    GREP = "grep"
    TREE = "tree"
    GLOB = "glob"


class VikingDBOperation(Enum):
    """VikingDB operations."""
    INSERT = "insert"
    UPDATE = "update"
    UPSERT = "upsert"
    DELETE = "delete"
    GET = "get"
    EXISTS = "exists"
    SEARCH = "search"
    FILTER = "filter"
    CREATE_COLLECTION = "create_collection"
    DROP_COLLECTION = "drop_collection"
    COLLECTION_EXISTS = "collection_exists"
    LIST_COLLECTIONS = "list_collections"


@dataclass
class AGFSCallRecord:
    """
    Record of a single AGFS client call.
    
    Used when recording VikingFS operations that may involve multiple AGFS calls.
    """
    operation: str
    request: Dict[str, Any]
    response: Optional[Any] = None
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


@dataclass
class IORecord:
    """
    Single IO operation record.

    Attributes:
        timestamp: ISO format timestamp
        io_type: IO type (fs or vikingdb)
        operation: Operation name
        request: Request parameters
        response: Response data (serialized)
        latency_ms: Latency in milliseconds
        success: Whether operation succeeded
        error: Error message if failed
        agfs_calls: List of AGFS calls made during this operation (for VikingFS operations)
    """
    timestamp: str
    io_type: str
    operation: str
    request: Dict[str, Any]
    response: Optional[Any] = None
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    agfs_calls: List[AGFSCallRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        
        def serialize_any(obj: Any) -> Any:
            """Recursively serialize any object."""
            if obj is None:
                return None
            if isinstance(obj, bytes):
                return {"__bytes__": obj.decode("utf-8", errors="replace")}
            if isinstance(obj, dict):
                return {k: serialize_any(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [serialize_any(item) for item in obj]
            if isinstance(obj, (str, int, float, bool)):
                return obj
            if hasattr(obj, "__dict__"):
                return serialize_any(obj.__dict__)
            return str(obj)
        
        data = asdict(self)
        data["response"] = serialize_any(data["response"])
        
        serialized_agfs_calls = []
        for call in data["agfs_calls"]:
            serialized_call = call.copy()
            serialized_call["request"] = serialize_any(serialized_call["request"])
            serialized_call["response"] = serialize_any(serialized_call["response"])
            serialized_agfs_calls.append(serialized_call)
        data["agfs_calls"] = serialized_agfs_calls
        
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IORecord":
        """Create from dictionary."""
        data = data.copy()
        if "agfs_calls" in data and data["agfs_calls"]:
            agfs_calls = []
            for call_data in data["agfs_calls"]:
                if isinstance(call_data, dict):
                    agfs_calls.append(AGFSCallRecord(**call_data))
                else:
                    agfs_calls.append(call_data)
            data["agfs_calls"] = agfs_calls
        return cls(**data)


class IORecorder:
    """
    Recorder for IO operations.

    Records all IO operations to a JSONL file for later playback.
    Thread-safe implementation.

    Usage:
        recorder = IORecorder(enabled=True)
        recorder.record_fs("read", {"uri": "viking://..."}, b"content", 10.5)

        # Or use as context manager
        with IORecorder.record_context("fs", "read", {"uri": "..."}) as r:
            result = fs.read(uri)
            r.set_response(result)
    """

    _instance: Optional["IORecorder"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        enabled: bool = False,
        records_dir: str = DEFAULT_RECORDS_DIR,
        record_file: Optional[str] = None,
    ):
        """
        Initialize IORecorder.

        Args:
            enabled: Whether recording is enabled
            records_dir: Directory to store record files
            record_file: Specific record file path (auto-generated if None)
        """
        self.enabled = enabled
        self.records_dir = Path(records_dir)
        self._file_lock = threading.Lock()

        if record_file:
            self.record_file = Path(record_file)
        else:
            self.records_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            self.record_file = self.records_dir / f"io_recorder_{date_str}.jsonl"

        if self.enabled:
            logger.info(f"[IORecorder] Recording enabled: {self.record_file}")

    @classmethod
    def get_instance(cls) -> "IORecorder":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = IORecorder()
        return cls._instance

    @classmethod
    def initialize(cls, enabled: bool = False, **kwargs) -> "IORecorder":
        """Initialize singleton instance."""
        with cls._lock:
            cls._instance = IORecorder(enabled=enabled, **kwargs)
        return cls._instance

    def _serialize_response(self, response: Any) -> Any:
        """Serialize response for JSON compatibility."""
        if response is None:
            return None
        if isinstance(response, bytes):
            return {"__bytes__": response.decode("utf-8", errors="replace")}
        if isinstance(response, dict):
            return {k: self._serialize_response(v) for k, v in response.items()}
        if isinstance(response, list):
            return [self._serialize_response(v) for v in response]
        if isinstance(response, (str, int, float, bool)):
            return response
        return str(response)

    def _write_record(self, record: IORecord) -> None:
        """Write record to file."""
        if not self.enabled:
            return

        with self._file_lock:
            with open(self.record_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def record_fs(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        agfs_calls: Optional[List[AGFSCallRecord]] = None,
    ) -> None:
        """
        Record a file system operation.

        Args:
            operation: Operation name (read, write, ls, stat, etc.)
            request: Request parameters
            response: Response data
            latency_ms: Latency in milliseconds
            success: Whether operation succeeded
            error: Error message if failed
            agfs_calls: List of AGFS calls made during this operation
        """
        record = IORecord(
            timestamp=datetime.now().isoformat(),
            io_type=IOType.FS.value,
            operation=operation,
            request=self._serialize_response(request),
            response=self._serialize_response(response),
            latency_ms=latency_ms,
            success=success,
            error=error,
            agfs_calls=agfs_calls or [],
        )
        self._write_record(record)

    def record_vikingdb(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        agfs_calls: Optional[List[AGFSCallRecord]] = None,
    ) -> None:
        """
        Record a VikingDB operation.

        Args:
            operation: Operation name (upsert, search, filter, etc.)
            request: Request parameters
            response: Response data
            latency_ms: Latency in milliseconds
            success: Whether operation succeeded
            error: Error message if failed
            agfs_calls: List of AGFS calls made during this operation
        """
        record = IORecord(
            timestamp=datetime.now().isoformat(),
            io_type=IOType.VIKINGDB.value,
            operation=operation,
            request=self._serialize_response(request),
            response=self._serialize_response(response),
            latency_ms=latency_ms,
            success=success,
            error=error,
            agfs_calls=agfs_calls or [],
        )
        self._write_record(record)

    def get_records(self) -> List[IORecord]:
        """Read all records from file."""
        records = []
        if not self.record_file.exists():
            return records

        with open(self.record_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(IORecord.from_dict(json.loads(line)))
        return records

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics of recorded operations."""
        records = self.get_records()

        stats = {
            "total_count": len(records),
            "fs_count": 0,
            "vikingdb_count": 0,
            "total_latency_ms": 0.0,
            "operations": {},
            "errors": 0,
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

            if not record.success:
                stats["errors"] += 1

        return stats


class RecordContext:
    """Context manager for recording operations with timing."""

    def __init__(
        self,
        recorder: IORecorder,
        io_type: str,
        operation: str,
        request: Dict[str, Any],
    ):
        self.recorder = recorder
        self.io_type = io_type
        self.operation = operation
        self.request = request
        self.response = None
        self.error = None
        self.success = True
        self.agfs_calls: List[AGFSCallRecord] = []
        self._start_time = None

    def __enter__(self):
        self._start_time = datetime.now()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (datetime.now() - self._start_time).total_seconds() * 1000

        if exc_type is not None:
            self.success = False
            self.error = str(exc_val)

        if self.io_type == IOType.FS.value:
            self.recorder.record_fs(
                operation=self.operation,
                request=self.request,
                response=self.response,
                latency_ms=latency_ms,
                success=self.success,
                error=self.error,
                agfs_calls=self.agfs_calls,
            )
        else:
            self.recorder.record_vikingdb(
                operation=self.operation,
                request=self.request,
                response=self.response,
                latency_ms=latency_ms,
                success=self.success,
                error=self.error,
                agfs_calls=self.agfs_calls,
            )

        return False

    def set_response(self, response: Any) -> None:
        """Set the response data."""
        self.response = response
        
    def add_agfs_call(
        self,
        operation: str,
        request: Dict[str, Any],
        response: Any = None,
        latency_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """
        Add an AGFS call to this operation record.
        
        Args:
            operation: AGFS operation name
            request: Request parameters
            response: Response data
            latency_ms: Latency in milliseconds
            success: Whether operation succeeded
            error: Error message if failed
        """
        call = AGFSCallRecord(
            operation=operation,
            request=request,
            response=response,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )
        self.agfs_calls.append(call)


def get_recorder() -> IORecorder:
    """Get the global IORecorder instance."""
    return IORecorder.get_instance()


def init_recorder(enabled: bool = False, **kwargs) -> IORecorder:
    """Initialize the global IORecorder instance."""
    return IORecorder.initialize(enabled=enabled, **kwargs)


def create_recording_agfs_client(agfs_client: Any, record_file: Optional[str] = None) -> Any:
    """
    Create a recording wrapper for AGFSClient.

    This function wraps an AGFSClient with recording capabilities.
    The wrapper records all IO operations to a file for later playback.

    Args:
        agfs_client: The underlying AGFSClient instance
        record_file: Path to the record file (uses default if None)

    Returns:
        RecordingAGFSClient instance if recorder is enabled, otherwise the original client

    Usage:
        from openviking.eval.recorder import init_recorder, create_recording_agfs_client
        from pyagfs import AGFSClient

        # Initialize recorder
        init_recorder(enabled=True)

        # Create recording client
        base_client = AGFSClient(api_base_url="http://localhost:1833")
        recording_client = create_recording_agfs_client(base_client)

        # Use in VikingFS
        viking_fs = VikingFS(...)
        viking_fs.agfs = recording_client
    """
    from openviking.eval.recorder.recording_client import RecordingAGFSClient

    recorder = get_recorder()

    if not recorder.enabled:
        return agfs_client

    record_path = record_file or str(recorder.record_file)
    return RecordingAGFSClient(agfs_client, record_path)


# Export RecordingVikingFS and RecordingVikingDB
from openviking.eval.recorder.wrapper import RecordingVikingFS, RecordingVikingDB
