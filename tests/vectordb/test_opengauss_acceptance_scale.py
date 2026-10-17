"""Opt-in, isolated adapter-level quality/plan/latency acceptance; never HTTP.

Only OPENVIKING_OPENGAUSS_FEATURE_DSN is read, and only the disposable
127.0.0.1:35436/postgres target is accepted. No credentials enter the report.
"""

import json
import math
import os
import random
import struct
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import pytest
from test_opengauss_acceptance_helper import (  # noqa: F401
    acceptance_isolation,
    acceptance_target,
    analyze_acceptance,
)

from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig

pytestmark = pytest.mark.integration

_RECORDS = 10_000
_BUDGET = 1000
_ALPHA = 0.37
_TOP_K = 20
_REPEATS = 30
_WORDS = ("quasar", "zephyr", "obsidian", "narwhal", "saffron")
_ORIGIN = datetime(2026, 10, 16, tzinfo=timezone.utc)
_QUERY = [0.31, -0.27, 0.53, 0.19, -0.41, 0.61, 0.13, -0.23]
_HYBRID_SPARSE = {"common": 0.65, "unique000012": 0.4}


def _scope(tenant, cohort=False):
    tenant_filter = {"op": "must", "field": "account_id", "conds": [tenant]}
    if not cohort:
        return tenant_filter
    return {"op": "and", "conds": [tenant_filter, {
        "op": "must", "field": "cohort", "conds": ["controlled"],
    }]}


def _float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def _records():
    rng = random.Random(20261016)
    records = []
    for i in range(_RECORDS):
        word = _WORDS[i // 48] if i < 48 * len(_WORDS) else None
        sparse = {f"unique{i:06d}": 1.0, "common": 0.1 + rng.random()}
        if word:
            sparse[word] = 0.5 + rng.random()
        vector = [_float32(rng.uniform(-1, 1)) for _ in range(8)]
        records.append({
            "id": f"record{i:06d}",
            "uri": f"viking://resources/scale/record{i:06d}",
            "account_id": "a" if i % 2 == 0 else "b",
            "cohort": "controlled" if i < 1600 else "background",
            "updated_at": (_ORIGIN - timedelta(hours=i % 721)).isoformat(),
            "content": f"English archive document serial{i:06d} " + (word or "ordinary"),
            "sparse_vector": sparse,
            "vector": vector,
        })
    return records


def _baseline(records, *, tenant, dense=None, sparse=None, cohort=False, decay=False):
    ranked = []
    query_norm = math.sqrt(sum(value * value for value in dense)) if dense else None
    for row in records:
        if row["account_id"] != tenant or (cohort and row["cohort"] != "controlled"):
            continue
        sparse_score = sum(row["sparse_vector"].get(term, 0) * weight
                           for term, weight in (sparse or {}).items())
        if sparse and not dense and sparse_score <= 0:
            continue
        cosine = 0.0
        if dense:
            vector = row["vector"]
            cosine = sum(a * b for a, b in zip(vector, dense, strict=True)) / (
                math.sqrt(sum(value * value for value in vector)) * query_norm
            )
        origin_score = ((1 - _ALPHA) * cosine + _ALPHA * sparse_score
                        if dense and sparse else cosine if dense else sparse_score)
        factor = 1.0
        if decay:
            age_days = abs((_ORIGIN - datetime.fromisoformat(row["updated_at"])).total_seconds()) / 86400
            factor = 0.5 ** (max(0.0, age_days - 1.0) / 7.0)
        ranked.append({"id": row["id"], "score": origin_score * factor,
                       "origin_score": origin_score, "time_factor": factor})
    return sorted(ranked, key=lambda row: (-row["score"], row["id"]))


def _exact_ids(hits, expected, tenant):
    actual = [hit["id"] for hit in hits]
    actual_set, expected_set = set(actual), set(expected)
    metrics = {
        "expected_ids": sorted(expected_set), "actual_ids": actual,
        "recall": len(actual_set & expected_set) / len(expected_set) if expected_set else 1.0,
        "precision": len(actual_set & expected_set) / len(actual) if actual else 0.0,
    }
    assert len(actual) == len(actual_set), "duplicate IDs"
    assert all(hit["account_id"] == tenant for hit in hits), "tenant isolation violation"
    assert actual_set == expected_set, (
        f"exact IDs differ: missing={sorted(expected_set - actual_set)}, "
        f"unexpected={sorted(actual_set - expected_set)}"
    )
    assert metrics["recall"] == metrics["precision"] == 1.0
    return metrics


def _top_k(hits, expected, tenant, decay=False):
    expected = expected[:_TOP_K]
    assert all(hit["account_id"] == tenant for hit in hits), "tenant isolation violation"
    assert [hit["id"] for hit in hits] == [row["id"] for row in expected], (
        f"TopK IDs differ: actual={[hit['id'] for hit in hits]}, "
        f"expected={[row['id'] for row in expected]}"
    )
    assert [hit["_score"] for hit in hits] == pytest.approx(
        [row["score"] for row in expected], rel=2e-6, abs=2e-7
    )
    if decay:
        assert [hit["_origin_score"] for hit in hits] == pytest.approx(
            [row["origin_score"] for row in expected], rel=2e-6, abs=2e-7
        )
        assert [hit["_time_score"] for hit in hits] == pytest.approx(
            [row["time_factor"] for row in expected], rel=1e-10, abs=1e-10
        )
    return {"top_k": _TOP_K, "recall": 1.0, "precision": 1.0,
            "expected": expected, "actual": hits}


class _RecordingCursor:
    """Delegate real execution; retain only the adapter's final retrieval SQL."""

    def __init__(self, cursor, captures):
        self._cursor = cursor
        self._captures = captures
        self._active = None

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, sql, params=None):
        self._active = None
        if any(marker in sql for marker in (
            "AS _ov_fulltext_score", "AS _ov_dense_distance", "AS _distance",
        )):
            self._active = {"sql": sql, "params": list(params or []), "returned_rows": None}
            self._captures.append(self._active)
        return self._cursor.execute(sql, params)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if self._active is not None:
            self._active["returned_rows"] = len(rows)
        return rows


def _capture_and_explain(collection, monkeypatch, call, *, dense=False):
    captures = []
    real_cursor = collection._cursor
    with monkeypatch.context() as patch:
        patch.setattr(collection, "_cursor", lambda: _RecordingCursor(real_cursor(), captures))
        hits = call()
    assert len(captures) == 1, f"expected one real retrieval statement, got {len(captures)}"
    capture = captures[0]
    cursor = real_cursor()
    try:
        if dense:
            collection._apply_search_params_on_cursor(cursor, "default")
        cursor.execute("EXPLAIN ANALYZE " + capture["sql"], capture["params"])
        capture["plan"] = "\n".join(str(row[0]) for row in cursor.fetchall())
        hint = f"/*+ indexscan ({collection._name} {collection._meta.get('_retrieval', {}).get('index', {}).get('name')}) */"
        capture["adapter_index_hint"] = hint in capture["sql"]
        natural_sql = capture["sql"].replace(hint, "")
        capture["natural_sql"] = natural_sql
        if capture["adapter_index_hint"]:
            cursor.execute("EXPLAIN ANALYZE " + natural_sql, capture["params"])
            capture["natural_plan"] = "\n".join(str(row[0]) for row in cursor.fetchall())
        else:
            capture["natural_plan"] = capture["plan"]
        collection._conn.commit()
    except Exception:
        collection._conn.rollback()
        raise
    finally:
        cursor.close()
    assert capture["plan"], "empty actual adapter EXPLAIN ANALYZE"
    capture["planner_overrides"] = False
    capture["search_settings"] = collection._search_param_statements("default") if dense else []
    return hits, capture


def _percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _safe_error(error):
    text = str(error)
    dsn = os.getenv("OPENVIKING_OPENGAUSS_FEATURE_DSN", "")
    if dsn:
        text = text.replace(dsn, "<redacted-dsn>")
        password = unquote(urlparse(dsn).password or "")
        if password:
            text = text.replace(password, "<redacted-password>")
    return {"type": type(error).__name__, "message": text}


def _measure(report, route, cases):
    entry = report["routes"].setdefault(route, {})
    samples, errors, quality = [], [], []
    successful_samples = []
    for i in range(_REPEATS):
        label, call, validate = cases[i % len(cases)]
        started = time.perf_counter()
        try:
            hits = call()
        except Exception as error:
            samples.append((time.perf_counter() - started) * 1000)
            errors.append({"iteration": i, "query": label, "stage": "adapter", **_safe_error(error)})
            continue
        elapsed = (time.perf_counter() - started) * 1000
        samples.append(elapsed)
        successful_samples.append(elapsed)
        try:
            result = validate(hits)
            if i < len(cases):
                quality.append({"query": label, **result})
        except Exception as error:
            errors.append({"iteration": i, "query": label, "stage": "quality", **_safe_error(error),
                           "actual_hits": hits})
    entry.update({
        "attempts": _REPEATS, "latency_ms": samples,
        "p50_ms": _percentile(samples, 0.50), "p95_ms": _percentile(samples, 0.95),
        "successful_call_p50_ms": _percentile(successful_samples, 0.5) if successful_samples else None,
        "successful_call_p95_ms": _percentile(successful_samples, 0.95) if successful_samples else None,
        "errors": errors, "error_rate": len(errors) / _REPEATS,
        "adapter_error_rate": sum(error["stage"] == "adapter" for error in errors) / _REPEATS,
        "quality_error_rate": sum(error["stage"] == "quality" for error in errors) / _REPEATS,
        "quality": quality,
    })
    if errors:
        report["failures"].append({"route": route, "errors": errors})


def _measure_concurrent(report, route, cases):
    def worker(worker_id):
        samples = []
        for iteration in range(8):
            label, call, validate = cases[(worker_id + iteration) % len(cases)]
            started = time.perf_counter()
            hits = call()
            samples.append((time.perf_counter() - started) * 1000)
            validate(hits)
        return samples

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker, i) for i in range(4)]
        samples = [sample for future in futures for sample in future.result(timeout=60)]
    assert len(samples) == 32
    report["routes"][route]["concurrent"] = {
        "workers": 4, "calls": len(samples), "errors": 0, "latency_ms": samples,
        "p50_ms": _percentile(samples, .5), "p95_ms": _percentile(samples, .95),
    }


@pytest.fixture
def scale_database(tmp_path, acceptance_isolation):  # noqa: F811
    parsed = acceptance_target()
    name = f"ov_scale_{uuid.uuid4().hex[:12]}"
    path = tmp_path / "opengauss_acceptance_scale.json"
    report = {
        "level": "adapter (not HTTP or end-to-end)", "collection": name,
        "records": _RECORDS, "vector_dim": 8, "tenant_records": {"a": 5000, "b": 5000},
        "controlled_records_per_tenant": 800, "candidate_limit_per_modality": _BUDGET,
        "alpha": _ALPHA, "repeats_per_route": _REPEATS, "concurrency": 1,
        "latency_scope": "warm sequential adapter call including DB, validation, decoding and Python rerank; excludes quality oracle and EXPLAIN",
        "percentile_method": "linear interpolation, position=(n-1)*p; all attempted calls",
        "baseline": "independent Python cosine + sparse dot product; (1-alpha)*cosine+alpha*dot; decay=0.5**(max(0,abs(age_days)-1)/7)",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "routes": {}, "failures": [], "cleanup": {},
    }
    adapter = None
    try:
        adapter = OpenGaussCollectionAdapter(name, OpenGaussConfig(
            host=parsed.hostname, port=parsed.port, user=unquote(parsed.username),
            password=unquote(parsed.password or ""), db_name="postgres", sparse_enabled=True,
            candidate_limit=_BUDGET, connection_pool_min_size=1, connection_pool_max_size=4,
            parallel_workers=0, maintenance_work_mem_mb=64,
            search_params={"ef_search": 1000},
        ))
        cursor = adapter._conn.cursor()
        try:
            cursor.execute("SELECT current_database(), current_user, current_setting('sql_compatibility'), version()")
            database, user, compatibility, version = cursor.fetchone()
            assert database == "postgres" and user == "gaussdb"
            report["database"] = {"name": database, "sql_compatibility": compatibility, "version": version}
            adapter._conn.commit()
        finally:
            cursor.close()
        adapter.create_collection(name, {"Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "cohort", "FieldType": "string"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "content", "FieldType": "text"},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 8},
        ], "ScalarIndex": ["account_id", "cohort"]},
            distance="cosine", sparse_weight=_ALPHA, index_name="default")
        collection = adapter.retrieval_collection()
        collection.migrate_content_schema(allow_ddl=True)
        records = _records()
        started = time.perf_counter()
        adapter.begin_bulk_ingest()
        try:
            for start in range(0, len(records), 500):
                assert len(adapter.upsert(records[start:start + 500])) == 500
        finally:
            adapter.end_bulk_ingest()
        report["ingest_and_dense_build_ms"] = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        collection.create_sparse_index(candidate_limit=_BUDGET, sparse_weight=_ALPHA)
        report["sparse_build_ms"] = (time.perf_counter() - started) * 1000
        assert collection.validate_sparse_index()
        assert collection.has_index("default")
        postings = collection._sparse_posting_table_name()
        analyze_acceptance(collection, name, postings)
        counts = collection._execute(
            f'SELECT count(*), count(DISTINCT content), count(vector), count(content) FROM "{name}"',
            fetch=True,
        )[0]
        assert list(counts) == [_RECORDS] * 4
        report["verified_counts"] = dict(zip(("records", "distinct_content", "vectors", "non_null_content"), counts, strict=True))
        report["posting_rows"] = collection._execute(f'SELECT count(*) FROM "{postings}"', fetch=True)[0][0]
        yield adapter, collection, records, report
    except Exception as error:
        report["uncaught_error"] = _safe_error(error)
        raise
    finally:
        try:
            if adapter is not None:
                adapter._conn.rollback()
                adapter.drop_collection()
                report["cleanup"]["collection_absent"] = not adapter._table_exists(name)
                cursor = adapter._conn.cursor()
                try:
                    cursor.execute(
                        "SELECT relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND position(%s in relname)>0",
                        (name,),
                    )
                    remaining = [row[0] for row in cursor.fetchall()]
                    report["cleanup"]["remaining_relations"] = remaining
                    adapter._conn.commit()
                finally:
                    cursor.close()
                assert report["cleanup"]["collection_absent"] and not remaining
        except Exception as error:
            report["cleanup"]["error"] = _safe_error(error)
            raise
        finally:
            if adapter is not None:
                adapter.close()
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nOPENGAUSS_ACCEPTANCE_SCALE_JSON={path}", flush=True)


def test_opengauss_acceptance_scale(scale_database, monkeypatch):
    adapter, collection, records, report = scale_database
    fields = ["account_id", "cohort", "updated_at"]

    def run_route(route, cases, dense=False, controlled=False):
        entry = report["routes"].setdefault(route, {})
        try:
            hits, capture = _capture_and_explain(collection, monkeypatch, cases[0][1], dense=dense)
            entry["explain"] = capture
            entry["warmup_quality"] = cases[0][2](hits)
            if route == "bm25":
                assert entry["index"]["name"] in capture["natural_plan"], (
                    "representative selective query must naturally use its fulltext index: "
                    + capture["natural_plan"]
                )
                entry["natural_fulltext_index_used"] = True
            if controlled:
                assert capture["returned_rows"] == 800, "candidate coverage must include the entire controlled tenant"
                assert capture["params"].count(_BUDGET) == 2
                entry["candidate_coverage"] = {"expected": 800, "actual": capture["returned_rows"], "complete": True}
        except Exception as error:
            entry["plan_or_warmup_error"] = _safe_error(error)
            report["failures"].append({"route": route, "stage": "plan_or_warmup", **_safe_error(error)})
        _measure(report, route, cases)
        try:
            _measure_concurrent(report, route, cases)
        except Exception as error:
            entry["concurrent_error"] = _safe_error(error)
            report["failures"].append({"route": route, "stage": "concurrent", **_safe_error(error)})

    try:
        started = time.perf_counter()
        collection.configure_fulltext("bm25")
        schema_state = collection.migrate_content_schema(allow_ddl=True)
        assert schema_state["schema_version"] == 2
        state = collection.build_fulltext_index("bm25", allow_ddl=True)
        assert collection.validate_fulltext_index("bm25")
        analyze_acceptance(collection, collection._name)
        report["routes"]["bm25"] = {
            "build_ms": (time.perf_counter() - started) * 1000,
            "index": state["index"],
        }
        cases = []
        for word in _WORDS:
            for tenant in ("a", "b"):
                expected = [row["id"] for row in records
                            if row["account_id"] == tenant and word in row["sparse_vector"]]
                assert 0 < len(expected) <= 100
                cases.append((f"{word}/tenant={tenant}",
                              lambda word=word, tenant=tenant: adapter.search_by_keywords(
                                  query=word, mode="bm25", fields=["content"], filter=_scope(tenant),
                                  limit=100, output_fields=["account_id", "content"],
                              ),
                              lambda hits, expected=expected, tenant=tenant: _exact_ids(hits, expected, tenant)))
        run_route("bm25", cases)
    except Exception as error:
        report["failures"].append({"route": "bm25", "stage": "lifecycle", **_safe_error(error)})

    rare_cases = []
    for word in _WORDS:
        for tenant in ("a", "b"):
            expected = [row["id"] for row in records
                        if row["account_id"] == tenant and word in row["sparse_vector"]]
            rare_cases.append((f"{word}/tenant={tenant}",
                               lambda word=word, tenant=tenant: adapter.query(
                                   sparse_query_vector={word: 1.0}, filter=_scope(tenant),
                                   limit=100, output_fields=fields,
                               ),
                               lambda hits, expected=expected, tenant=tenant: _exact_ids(hits, expected, tenant)))
    run_route("sparse_rare", rare_cases)
    unique_cases = []
    for i in (12, 13, 2012, 2013, 9998, 9999):
        tenant = "a" if i % 2 == 0 else "b"
        unique_cases.append((f"unique{i:06d}/tenant={tenant}",
                             lambda i=i, tenant=tenant: adapter.query(
                                 sparse_query_vector={f"unique{i:06d}": 1.0}, filter=_scope(tenant),
                                 limit=100, output_fields=fields,
                             ),
                             lambda hits, i=i, tenant=tenant: _exact_ids(hits, [f"record{i:06d}"], tenant)))
    run_route("sparse_unique", unique_cases)

    for route, dense, sparse, controlled, decay in (
        ("dense", _QUERY, None, False, False),
        ("sparse_common", None, {"common": 1.0}, False, False),
        ("hybrid", _QUERY, _HYBRID_SPARSE, True, False),
        ("hybrid_decay", _QUERY, _HYBRID_SPARSE, True, True),
    ):
        cases = []
        for tenant in ("a", "b"):
            expected = _baseline(records, tenant=tenant, dense=dense, sparse=sparse,
                                 cohort=controlled, decay=decay)
            if controlled:
                assert len(expected) == 800 < _BUDGET
            advance = {"time_decay": {"protection": "1d", "origin": _ORIGIN.isoformat()}} if decay else None
            cases.append((f"{route}/tenant={tenant}",
                          lambda tenant=tenant, dense=dense, sparse=sparse, controlled=controlled, advance=advance: adapter.query(
                              query_vector=dense, sparse_query_vector=sparse, filter=_scope(tenant, controlled),
                              limit=_TOP_K, output_fields=fields, advance=advance,
                          ),
                          lambda hits, expected=expected, tenant=tenant, decay=decay: _top_k(hits, expected, tenant, decay)))
        run_route(route, cases, dense=bool(dense), controlled=controlled)

    for route, entry in report["routes"].items():
        if "attempts" in entry:
            print(f"{route}: n={entry['attempts']} p50_ms={entry['p50_ms']:.3f} "
                  f"p95_ms={entry['p95_ms']:.3f} error_rate={entry['error_rate']:.3%}", flush=True)
            if "concurrent" in entry:
                concurrent = entry["concurrent"]
                print(f"{route} concurrency=4 n={concurrent['calls']} "
                      f"p50_ms={concurrent['p50_ms']:.3f} p95_ms={concurrent['p95_ms']:.3f}", flush=True)
    assert set(report["routes"]) == {
        "bm25", "dense", "sparse_rare", "sparse_unique", "sparse_common", "hybrid", "hybrid_decay",
    }
    assert not report["failures"], json.dumps(report["failures"], ensure_ascii=False)
