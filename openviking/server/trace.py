# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""HTTP router helpers for request-level tracing."""

from __future__ import annotations

from typing import Any, Dict

from openviking.trace import RequestTraceCollector


def create_collector(operation: str, enabled: bool) -> RequestTraceCollector:
    """Create request trace collector for an operation."""
    return RequestTraceCollector(operation=operation, enabled=enabled)


def inject_trace(result: Any, collector: RequestTraceCollector, status: str = "ok") -> Any:
    """Inject trace payload into a result dictionary."""
    trace_result = collector.finish(status=status)
    if trace_result is None:
        return result

    result_dict: Dict[str, Any]
    if result is None:
        result_dict = {}
    elif isinstance(result, dict):
        result_dict = result
    else:
        # Preserve existing behavior: routers only inject trace into dict results.
        result_dict = {"data": result}

    result_dict["trace"] = trace_result.to_dict()
    return result_dict
