# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from openviking.storage.recorder import (
    IORecorder,
    IORecord,
    IOType,
    init_recorder,
    get_recorder,
    create_recording_agfs_client,
)
from openviking.storage.recorder.async_writer import AsyncRecordWriter
from openviking.storage.recorder.recording_client import RecordingAGFSClient


class MockAGFSClient:
    """Mock AGFSClient for testing."""

    def __init__(self):
        self.calls = []

    def ls(self, path: str = "/"):
        self.calls.append(("ls", path))
        return [{"name": "test.txt", "size": 100}]

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        self.calls.append(("read", path, offset, size))
        return b"test content"

    def write(self, path: str, data: bytes, max_retries: int = 3):
        self.calls.append(("write", path, len(data)))
        return path

    def stat(self, path: str):
        self.calls.append(("stat", path))
        return {"name": "test.txt", "size": 100, "isDir": False}

    def mkdir(self, path: str, mode: str = "755"):
        self.calls.append(("mkdir", path, mode))
        return {"created": path}

    def rm(self, path: str, recursive: bool = False):
        self.calls.append(("rm", path, recursive))
        return {"removed": path}

    def mv(self, old_path: str, new_path: str):
        self.calls.append(("mv", old_path, new_path))
        return {"moved": old_path}

    def grep(self, path: str, pattern: str, recursive: bool = False, case_insensitive: bool = False, stream: bool = False):
        self.calls.append(("grep", path, pattern))
        return {"matches": []}


class TestAsyncRecordWriter:
    """Tests for AsyncRecordWriter."""

    def test_write_single_record(self):
        """Test writing a single record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")
            writer = AsyncRecordWriter(file_path)

            writer.write_record({"test": "data"})
            time.sleep(0.5)
            writer.stop()

            with open(file_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["test"] == "data"

    def test_write_batch_records(self):
        """Test writing multiple records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")
            writer = AsyncRecordWriter(file_path, batch_size=3)

            for i in range(5):
                writer.write_record({"id": i})

            time.sleep(0.5)
            writer.stop()

            with open(file_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 5

    def test_flush_on_stop(self):
        """Test that remaining records are flushed on stop."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")
            writer = AsyncRecordWriter(file_path, batch_size=100, flush_interval=10.0)

            writer.write_record({"test": "data"})
            writer.stop(timeout=2.0)

            with open(file_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 1


class TestRecordingAGFSClient:
    """Tests for RecordingAGFSClient."""

    def test_wrap_ls_operation(self):
        """Test wrapping ls operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")

            mock_client = MockAGFSClient()
            recording_client = RecordingAGFSClient(mock_client, file_path)

            result = recording_client.ls("/test")

            assert result == [{"name": "test.txt", "size": 100}]
            assert len(mock_client.calls) == 1

            recording_client.stop_recording()

            with open(file_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["io_type"] == "fs"
            assert record["operation"] == "ls"
            assert record["success"] is True
            assert record["latency_ms"] > 0

    def test_wrap_read_operation(self):
        """Test wrapping read operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")

            mock_client = MockAGFSClient()
            recording_client = RecordingAGFSClient(mock_client, file_path)

            result = recording_client.read("/test/file.txt")

            assert result == b"test content"

            recording_client.stop_recording()

            with open(file_path, "r") as f:
                lines = f.readlines()

            record = json.loads(lines[0])
            assert record["operation"] == "read"
            assert record["response"]["__bytes__"] == "test content"

    def test_wrap_multiple_operations(self):
        """Test wrapping multiple operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")

            mock_client = MockAGFSClient()
            recording_client = RecordingAGFSClient(mock_client, file_path, batch_size=1)

            recording_client.ls("/")
            recording_client.stat("/test.txt")
            recording_client.read("/test.txt")

            time.sleep(0.5)
            recording_client.stop_recording()

            with open(file_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 3
            operations = [json.loads(line)["operation"] for line in lines]
            assert operations == ["ls", "stat", "read"]

    def test_error_recording(self):
        """Test recording failed operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.jsonl")

            mock_client = MockAGFSClient()

            def failing_read(path, offset=0, size=-1, stream=False):
                raise FileNotFoundError("File not found")

            mock_client.read = failing_read

            recording_client = RecordingAGFSClient(mock_client, file_path)

            with pytest.raises(FileNotFoundError):
                recording_client.read("/nonexistent")

            time.sleep(0.3)
            recording_client.stop_recording()

            with open(file_path, "r") as f:
                lines = f.readlines()

            record = json.loads(lines[0])
            assert record["success"] is False
            assert "File not found" in record["error"]


class TestCreateRecordingAGFSClient:
    """Tests for create_recording_agfs_client function."""

    def test_create_with_recorder_enabled(self):
        """Test creating recording client when recorder is enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_recorder(enabled=True, records_dir=tmpdir)

            mock_client = MockAGFSClient()
            recording_client = create_recording_agfs_client(mock_client)

            assert isinstance(recording_client, RecordingAGFSClient)

            recording_client.stop_recording()

    def test_create_with_recorder_disabled(self):
        """Test creating recording client when recorder is disabled."""
        init_recorder(enabled=False)

        mock_client = MockAGFSClient()
        result = create_recording_agfs_client(mock_client)

        assert result is mock_client


class TestVikingFSRecorderIntegration:
    """Tests for VikingFS recorder integration."""

    def test_enable_viking_fs_recorder(self):
        """Test enabling recorder on VikingFS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_recorder(enabled=True, records_dir=tmpdir)

            from openviking.storage.viking_fs import VikingFS, _enable_viking_fs_recorder

            viking_fs = VikingFS(agfs_url="http://localhost:1833")
            _enable_viking_fs_recorder(viking_fs)

            assert hasattr(viking_fs.agfs, 'stop_recording')

            if hasattr(viking_fs.agfs, 'stop_recording'):
                viking_fs.agfs.stop_recording()
