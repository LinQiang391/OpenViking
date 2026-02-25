# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Request-level tracing primitives for HTTP/API workflows."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class TraceEvent:
    """Single structured trace event."""

    stage: str
    name: str
    ts_ms: float
    status: str = "ok"
    attrs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["ts_ms"] = round(float(data["ts_ms"]), 3)
        return data


@dataclass
class TraceResult:
    """Final request trace output."""

    summary: Dict[str, Any]
    events: List[TraceEvent]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "v1",
            "summary": self.summary,
            "events": [event.to_dict() for event in self.events],
        }


class TraceSummaryBuilder:
    """Build normalized summary metrics from collector data."""

    @staticmethod
    def _i(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def build(
        cls,
        *,
        trace_id: str,
        operation: str,
        status: str,
        duration_ms: float,
        counters: Dict[str, float],
        gauges: Dict[str, Any],
        error_stage: str,
        error_code: str,
        error_message: str,
        dropped_events: int,
    ) -> Dict[str, Any]:
        vector_candidates_scored = cls._i(counters.get("vector.candidates_scored"), 0)
        vectors_scanned = gauges.get("vector.vectors_scanned")
        if vectors_scanned is None:
            vectors_scanned = cls._i(counters.get("vector.vectors_scanned"), 0)

        memory_extracted = gauges.get("memory.memories_extracted")
        if memory_extracted is None and counters.get("memory.memories_extracted") is not None:
            memory_extracted = cls._i(counters.get("memory.memories_extracted"), 0)

        return {
            "trace_id": trace_id,
            "operation": operation,
            "status": status,
            "total_duration_ms": round(float(duration_ms), 3),
            "token_usage": {
                "input_tokens": cls._i(counters.get("token.input_tokens"), 0),
                "output_tokens": cls._i(counters.get("token.output_tokens"), 0),
                "total_tokens": cls._i(counters.get("token.total_tokens"), 0),
            },
            "vector": {
                "search_calls": cls._i(counters.get("vector.search_calls"), 0),
                "candidates_scored": vector_candidates_scored,
                "candidates_after_threshold": cls._i(
                    counters.get("vector.candidates_after_threshold"), 0
                ),
                "returned": cls._i(
                    gauges.get("vector.returned", counters.get("vector.returned")), 0
                ),
                "vectors_scanned": vectors_scanned,
                "scan_unavailable_reason": gauges.get("vector.scan_unavailable_reason", ""),
            },
            "semantic_nodes": {
                "total_nodes": gauges.get("semantic_nodes.total_nodes"),
                "done_nodes": gauges.get("semantic_nodes.done_nodes"),
                "pending_nodes": gauges.get("semantic_nodes.pending_nodes"),
                "in_progress_nodes": gauges.get("semantic_nodes.in_progress_nodes"),
            },
            "memory": {
                "memories_extracted": memory_extracted,
            },
            "errors": {
                "error_stage": error_stage,
                "error_code": error_code,
                "message": error_message,
            },
            "events_truncated": dropped_events > 0,
            "dropped_events": dropped_events,
        }


class RequestTraceCollector:
    """Request-scoped trace collector with low-overhead disabled mode."""

    def __init__(self, operation: str, enabled: bool = False, max_events: int = 500):
        self.operation = operation
        self.enabled = enabled
        self.trace_id = f"tr_{uuid4().hex}" if enabled else ""
        self.max_events = max_events
        self._start_time = time.perf_counter()
        self._events: List[TraceEvent] = []
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, Any] = {}
        self._dropped_events = 0
        self._error_stage = ""
        self._error_code = ""
        self._error_message = ""
        self._lock = Lock()

    def event(
        self,
        stage: str,
        name: str,
        attrs: Optional[Dict[str, Any]] = None,
        status: str = "ok",
    ) -> None:
        if not self.enabled:
            return

        with self._lock:
            if len(self._events) >= self.max_events:
                self._dropped_events += 1
                return
            event = TraceEvent(
                stage=stage,
                name=name,
                ts_ms=(time.perf_counter() - self._start_time) * 1000,
                status=status,
                attrs=attrs or {},
            )
            self._events.append(event)

    def count(self, key: str, delta: float = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._counters[key] += delta

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._gauges[key] = value

    def add_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        if not self.enabled:
            return
        self.count("token.input_tokens", max(input_tokens, 0))
        self.count("token.output_tokens", max(output_tokens, 0))
        self.count("token.total_tokens", max(input_tokens, 0) + max(output_tokens, 0))

    def set_error(self, stage: str, code: str, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._error_stage = stage
            self._error_code = code
            self._error_message = message

    def finish(self, status: str = "ok") -> Optional[TraceResult]:
        if not self.enabled:
            return None

        duration_ms = (time.perf_counter() - self._start_time) * 1000
        with self._lock:
            summary = TraceSummaryBuilder.build(
                trace_id=self.trace_id,
                operation=self.operation,
                status=status,
                duration_ms=duration_ms,
                counters=dict(self._counters),
                gauges=dict(self._gauges),
                error_stage=self._error_stage,
                error_code=self._error_code,
                error_message=self._error_message,
                dropped_events=self._dropped_events,
            )
            events = list(self._events)
        return TraceResult(summary=summary, events=events)
