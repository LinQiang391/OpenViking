# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import tempfile
from pathlib import Path

import pytest

from openviking.storage.recorder import (
    IORecorder,
    IORecord,
    IOType,
    init_recorder,
    get_recorder,
)
from openviking.eval.ragas import RagasConfig
from openviking.storage.recorder.playback import (
    IOPlayback,
    PlaybackStats,
    load_records,
    get_record_stats,
)


def test_io_record():
    """Test IORecord dataclass."""
    record = IORecord(
        timestamp="2026-02-14T12:00:00",
        io_type=IOType.FS.value,
        operation="read",
        request={"uri": "viking://test"},
        response={"content": "test"},
        latency_ms=10.5,
        success=True,
    )

    d = record.to_dict()
    assert d["io_type"] == "fs"
    assert d["operation"] == "read"
    assert d["latency_ms"] == 10.5

    record2 = IORecord.from_dict(d)
    assert record2.io_type == record.io_type
    assert record2.operation == record.operation


def test_io_recorder_disabled():
    """Test IORecorder when disabled."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=False, records_dir=tmpdir)
        recorder.record_fs("read", {"uri": "test"}, "response", 10.0)

        records = recorder.get_records()
        assert len(records) == 0


def test_io_recorder_enabled():
    """Test IORecorder when enabled."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=True, records_dir=tmpdir)

        recorder.record_fs("read", {"uri": "test1"}, "response1", 10.0)
        recorder.record_fs("write", {"uri": "test2"}, "response2", 20.0)
        recorder.record_vikingdb("search", {"query": "test"}, ["result"], 30.0)

        records = recorder.get_records()
        assert len(records) == 3

        stats = recorder.get_stats()
        assert stats["total_count"] == 3
        assert stats["fs_count"] == 2
        assert stats["vikingdb_count"] == 1
        assert stats["total_latency_ms"] == 60.0


def test_io_recorder_singleton():
    """Test IORecorder singleton pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder1 = init_recorder(enabled=False, records_dir=tmpdir)
        recorder2 = get_recorder()
        assert recorder1 is recorder2


def test_io_recorder_serialize_response():
    """Test response serialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=True, records_dir=tmpdir)

        recorder.record_fs("read", {"uri": "test"}, b"binary content", 10.0)
        recorder.record_fs("read", {"uri": "test"}, {"key": ["list", "of", "values"]}, 10.0)

        records = recorder.get_records()
        assert len(records) == 2
        assert "__bytes__" in records[0].response
        assert records[1].response["key"] == ["list", "of", "values"]


def test_io_recorder_error_recording():
    """Test recording failed operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=True, records_dir=tmpdir)

        recorder.record_fs(
            "read",
            {"uri": "test"},
            None,
            5.0,
            success=False,
            error="File not found",
        )

        records = recorder.get_records()
        assert len(records) == 1
        assert records[0].success is False
        assert records[0].error == "File not found"

        stats = recorder.get_stats()
        assert stats["errors"] == 1


def test_io_recorder_file_naming():
    """Test record file naming."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=True, records_dir=tmpdir)
        assert "io_recorder_" in str(recorder.record_file)
        assert recorder.record_file.suffix == ".jsonl"


def test_io_recorder_custom_file():
    """Test custom record file path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        custom_path = os.path.join(tmpdir, "custom_records.jsonl")
        recorder = IORecorder(enabled=True, record_file=custom_path)

        recorder.record_fs("read", {"uri": "test"}, "response", 10.0)

        assert Path(custom_path).exists()
        records = recorder.get_records()
        assert len(records) == 1


def test_load_records():
    """Test loading records from file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=True, records_dir=tmpdir)

        recorder.record_fs("read", {"uri": "test1"}, "response1", 10.0)
        recorder.record_fs("write", {"uri": "test2"}, "response2", 20.0)

        records = load_records(str(recorder.record_file))
        assert len(records) == 2


def test_get_record_stats():
    """Test getting statistics from record file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = IORecorder(enabled=True, records_dir=tmpdir)

        recorder.record_fs("read", {"uri": "test1"}, "response1", 10.0)
        recorder.record_fs("read", {"uri": "test2"}, "response2", 20.0)
        recorder.record_vikingdb("search", {"query": "test"}, ["result"], 30.0)

        stats = get_record_stats(str(recorder.record_file))
        assert stats["total_records"] == 3
        assert stats["fs_count"] == 2
        assert stats["vikingdb_count"] == 1
        assert stats["total_latency_ms"] == 60.0
        assert "fs.read" in stats["operations"]
        assert stats["operations"]["fs.read"]["count"] == 2


def test_playback_stats():
    """Test PlaybackStats dataclass."""
    stats = PlaybackStats(
        total_records=10,
        success_count=8,
        error_count=2,
        total_original_latency_ms=100.0,
        total_playback_latency_ms=50.0,
    )

    d = stats.to_dict()
    assert d["total_records"] == 10
    assert d["success_count"] == 8
    assert d["speedup_ratio"] == 2.0


def test_ragas_config_defaults():
    """Test RagasConfig default values."""
    config = RagasConfig()
    assert config.max_workers == 16
    assert config.batch_size == 10
    assert config.timeout == 180
    assert config.max_retries == 3


def test_ragas_config_from_env(monkeypatch):
    """Test RagasConfig from environment variables."""
    monkeypatch.setenv("RAGAS_MAX_WORKERS", "8")
    monkeypatch.setenv("RAGAS_BATCH_SIZE", "5")
    monkeypatch.setenv("RAGAS_TIMEOUT", "120")
    monkeypatch.setenv("RAGAS_MAX_RETRIES", "2")

    config = RagasConfig.from_env()
    assert config.max_workers == 8
    assert config.batch_size == 5
    assert config.timeout == 120
    assert config.max_retries == 2
