# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Contextvar helpers for request-level trace collectors."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from .request_trace import RequestTraceCollector

_NOOP_COLLECTOR = RequestTraceCollector(operation="noop", enabled=False)
_TRACE_COLLECTOR: contextvars.ContextVar[RequestTraceCollector] = contextvars.ContextVar(
    "openviking_request_trace_collector",
    default=_NOOP_COLLECTOR,
)


def get_trace_collector() -> RequestTraceCollector:
    """Get current request trace collector or disabled no-op collector."""
    return _TRACE_COLLECTOR.get()


@contextmanager
def bind_trace_collector(collector: RequestTraceCollector) -> Iterator[RequestTraceCollector]:
    """Bind collector to current context for the lifetime of the context manager."""
    token = _TRACE_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _TRACE_COLLECTOR.reset(token)
