# Request-Level Trace Design

## Overview

OpenViking now supports request-level trace output for:

- `POST /api/v1/search/find`
- `POST /api/v1/search/search`
- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/sessions/{session_id}/commit`

Enable tracing with `trace: true` in the request body.  
By default, tracing is disabled and existing behavior is unchanged.

## Usage

### HTTP

```bash
curl -X POST http://localhost:8080/api/v1/search/find \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "trace": true
  }'
```

```bash
curl -X POST http://localhost:8080/api/v1/resources \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/tmp/sample.md",
    "wait": true,
    "trace": true
  }'
```

### Python SDK

```python
result = await client.find("memory dedup", trace=True)
print(result.trace["summary"]["total_duration_ms"])
```

```python
session = client.session()
commit_result = await session.commit(trace=True)
print(commit_result["trace"]["summary"]["memory"]["memories_extracted"])
```

## Trace Output

`result.trace` always contains:

- `schema_version`
- `summary`
- `events`

Example:

```json
{
  "schema_version": "v1",
  "summary": {
    "trace_id": "tr_9f6f...",
    "operation": "search.find",
    "status": "ok",
    "total_duration_ms": 31.224,
    "token_usage": {
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0
    },
    "vector": {
      "search_calls": 3,
      "candidates_scored": 26,
      "candidates_after_threshold": 8,
      "returned": 5,
      "vectors_scanned": 26,
      "scan_unavailable_reason": ""
    },
    "semantic_nodes": {
      "total_nodes": null,
      "done_nodes": null,
      "pending_nodes": null,
      "in_progress_nodes": null
    },
    "memory": {
      "memories_extracted": null
    },
    "errors": {
      "error_stage": "",
      "error_code": "",
      "message": ""
    },
    "events_truncated": false,
    "dropped_events": 0
  },
  "events": [
    {
      "stage": "retriever.global_search",
      "name": "global_search_done",
      "ts_ms": 8.512,
      "status": "ok",
      "attrs": {
        "hits": 3
      }
    }
  ]
}
```

## Metric Definitions

- `token_usage.input_tokens/output_tokens/total_tokens`: request-scoped token usage
- `vector.search_calls`: number of vector retrieval calls in the request
- `vector.candidates_scored`: number of retrieved candidates evaluated
- `vector.candidates_after_threshold`: candidates that pass threshold filtering
- `vector.returned`: final returned item count
- `vector.vectors_scanned`: currently observed scanned-hit count on the request path
- `semantic_nodes.*`: request-level semantic DAG stats (mainly `add_resource(wait=true)`)
- `memory.memories_extracted`: extracted memory count (`session.commit` only)

For non-applicable operations, fields are returned as `null` when appropriate.

## Class Design

### Core Classes

- `RequestTraceCollector` (`openviking/trace/request_trace.py`)
  - Collects events/counters/gauges per request
  - Supports disabled no-op mode
- `TraceSummaryBuilder`
  - Normalizes counters into stable `summary` schema
- `TraceResult`
  - Standard output object with `to_dict()`
- `TraceContext` helpers (`openviking/trace/context.py`)
  - `bind_trace_collector(...)`
  - `get_trace_collector()`

### Router Integration

- `openviking/server/trace.py`
  - `create_collector(operation, enabled)`
  - `inject_trace(result, collector, status="ok")`

Routers create and bind collectors around request handling and inject trace into `result`.

### Internal Integration Points

- Search pipeline:
  - `openviking/service/search_service.py`
  - `openviking/storage/viking_fs.py`
  - `openviking/retrieve/hierarchical_retriever.py`
- Resource/skill pipeline:
  - `openviking/service/resource_service.py`
  - `openviking/utils/resource_processor.py`
  - `openviking/utils/skill_processor.py`
  - `openviking/storage/queuefs/semantic_processor.py`
- Session commit:
  - `openviking/service/session_service.py`
  - `openviking/session/session.py`
- Token usage hook:
  - `openviking/models/vlm/base.py` via `update_token_usage`

## Extensibility

To add trace support for a new endpoint:

1. Add `trace: bool = False` to request model.
2. Wrap endpoint logic with collector binding.
3. Add instrumentation by calling `get_trace_collector()`.
4. Use counters/gauges under new keys; `TraceSummaryBuilder` can be extended as needed.

The schema is versioned with `schema_version` for forward-compatible evolution.
