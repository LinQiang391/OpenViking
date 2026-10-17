# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit, collection-wide native BM25 lifecycle."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult

from .capabilities import probe_fulltext_capabilities
from .migration import OpenGaussContentMigrationMixin
from .sql import _bounded_identifier, _build_where_clause, _quote_identifier, _validate_identifier

# Bounds candidate materialization, not the tie group: an unresolved boundary
# at this limit fails closed rather than returning an incomplete, unstable page.
_BM25_MAX_CANDIDATES = 100_000


class OpenGaussFulltextMixin(OpenGaussContentMigrationMixin):
    """Place before ICollection in collection's bases; no constructor or implicit DDL.

    Uses existing _execute/_lock/_cursor, date-filter normalization and row
    conversion. Retrieval state lives exclusively in _meta['_retrieval']; legacy
    FullText index metadata never implies a ready physical index. Each collection
    supports only native BM25 for full-text retrieval.
    """

    def probe_fulltext_capabilities(self):
        with self._lock:
            return probe_fulltext_capabilities(self._conn)

    @staticmethod
    def _require_bm25_metadata(state: dict[str, Any]):
        spec = state.get("index")
        if state.get("engine") not in {None, "disabled", "bm25"} or (
            spec is not None and spec.get("engine") != "bm25"
        ):
            raise RuntimeError(
                "Non-BM25 full-text metadata requires manual cleanup of metadata and indexes; "
                "automatic conversion is forbidden"
            )

    def configure_fulltext(self, engine: str = "bm25"):
        """Persist an explicit engine choice without changing the table or indexes."""
        if engine not in {"disabled", "bm25"}:
            raise ValueError("engine must be disabled or bm25; automatic fallback is forbidden")
        with self._lock:
            self._require_bm25_metadata(self._retrieval_state())
            capabilities = None
            if engine != "disabled":
                capabilities = self.probe_fulltext_capabilities()
                status = capabilities[engine]["state"]
                if status != "available":
                    raise RuntimeError(f"Full-text engine {engine!r} is not available: {status}")

            def configure(cursor, state):
                self._require_bm25_metadata(state)
                unchanged = state.get("engine") == engine
                state["engine"] = engine
                state["capabilities"] = capabilities
                state.setdefault("generation", 0)
                if not unchanged or engine == "disabled":
                    state["state"] = "disabled" if engine == "disabled" else "configured"
                return deepcopy(state)

            return self._retrieval_transaction(configure)

    def _fulltext_index_rows(self, spec: dict[str, Any], cursor=None):
        sql = """SELECT i.indisvalid, i.indisready, a.amname,
                        pg_catalog.pg_get_indexdef(i.indexrelid, 1, true),
                        i.indpred IS NULL, i.indnatts
                 FROM pg_catalog.pg_index i
                 JOIN pg_catalog.pg_class t ON t.oid = i.indrelid
                 JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
                 JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid
                 JOIN pg_catalog.pg_am a ON a.oid = x.relam
                 WHERE n.nspname = current_schema() AND t.relname = %s AND x.relname = %s"""
        params = (self._name, spec["name"])
        if cursor is None:
            return self._execute(sql, params, fetch=True)
        cursor.execute(sql, params)
        return cursor.fetchall()

    def _fulltext_index_matches(
        self, spec: dict[str, Any], cursor=None, *, require_valid: bool = True
    ) -> bool:
        if spec.get("engine") != "bm25":
            return False
        rows = self._fulltext_index_rows(spec, cursor)
        if len(rows) != 1:
            return False
        valid, ready, method, expression, unconditional, count = rows[0]
        return bool(
            (not require_valid or (valid and ready))
            and unconditional
            and count == 1
            and method == "bm25"
            and expression.strip() in {"content", '"content"'}
        )

    def validate_fulltext_index(self, engine: str | None = None) -> bool:
        """Read the real table/index catalog on every check, not legacy FullText metadata."""
        if engine is not None and engine != "bm25":
            raise ValueError("engine must be bm25")
        with self._lock:
            state = self._retrieval_state()
            selected = engine if engine is not None else state.get("engine")
            spec = state.get("index")
            if (
                state.get("state") != "ready"
                or selected != "bm25"
                or selected != state.get("engine")
                or not spec
                or spec.get("engine") != selected
                or spec.get("generation") != state.get("generation")
            ):
                return False
            if self._get_column_types().get("content") != "text":
                return False
            return self._fulltext_index_matches(spec)

    def build_fulltext_index(
        self, engine: str | None = None, *, allow_ddl: bool = False, rebuild: bool = False
    ) -> dict[str, Any]:
        """Transactional CREATE + catalog validation + state; rebuild swaps a new generation.

        No CONCURRENTLY, auto-extension installation or distributed DDL is attempted.
        Failures roll back the candidate index and retain the previous generation.
        """
        self._require_fulltext_ddl(allow_ddl)
        if engine is not None and engine != "bm25":
            raise ValueError("engine must be bm25")
        with self._lock:
            state = self._retrieval_state()
            self._require_bm25_metadata(state)
            selected = engine if engine is not None else state.get("engine")
            if selected != "bm25" or selected != state.get("engine"):
                raise ValueError("Configure the requested BM25 full-text engine explicitly first")
            if getattr(self, "_distributed", False):
                raise NotImplementedError(
                    "Full-text DDL on distributed collections is not verified"
                )
            if not state.get("schema_ready") or self._get_column_types().get("content") != "text":
                raise RuntimeError("Explicit content schema migration is required")
            if not rebuild and self.validate_fulltext_index(selected):
                return state
            capabilities = self.probe_fulltext_capabilities()
            if capabilities[selected]["state"] != "available":
                raise RuntimeError(f"Full-text engine {selected!r} is unavailable")
            generation = state.get("generation", 0) + 1
            spec = {
                "name": _bounded_identifier(f"{self._name}_ov_{selected}_g{generation}"),
                "engine": selected,
                "generation": generation,
            }
            empty_guard = (
                ' AND NOT "_ov_content_empty"'
                if "_ov_content_empty" in self._get_column_types()
                else ""
            )

            def build(cursor, next_state):
                # SHARE blocks concurrent writes until the completeness check,
                # index build and metadata commit have all finished.
                cursor.execute(f"LOCK TABLE {_quote_identifier(self._name)} IN SHARE MODE")
                self._require_bm25_metadata(next_state)
                previous = next_state.get("index")
                previous_exists = previous and self._fulltext_index_rows(previous, cursor)
                if previous_exists and not self._fulltext_index_matches(
                    previous, cursor, require_valid=False
                ):
                    raise RuntimeError(
                        "Previous index is not the expected BM25/content index; "
                        "manual cleanup is required; refusing to replace or drop it"
                    )
                cursor.execute(
                    f"SELECT EXISTS (SELECT 1 FROM {_quote_identifier(self._name)} "
                    f'WHERE "content" IS NULL{empty_guard})'
                )
                if cursor.fetchone()[0]:
                    raise RuntimeError("Content backfill is incomplete; NULL content remains")
                cursor.execute(
                    f"CREATE INDEX {_quote_identifier(spec['name'])} "
                    f'ON {_quote_identifier(self._name)} USING bm25 ("content")'
                )
                if not self._fulltext_index_matches(spec, cursor):
                    raise RuntimeError("New full-text index failed physical catalog validation")
                if previous_exists and previous["name"] != spec["name"]:
                    if not self._fulltext_index_matches(previous, cursor, require_valid=False):
                        raise RuntimeError(
                            "Previous index identity changed; manual cleanup is required; "
                            "refusing to drop it"
                        )
                    cursor.execute(f"DROP INDEX {_quote_identifier(previous['name'])}")
                next_state.update(
                    {
                        "engine": selected,
                        "state": "ready",
                        "generation": generation,
                        "index": spec,
                        "backfill_complete": True,
                        "capabilities": capabilities,
                    }
                )
                return deepcopy(next_state)

            return self._retrieval_transaction(build)

    def rebuild_fulltext_index(self, engine: str | None = None, *, allow_ddl: bool = False):
        return self.build_fulltext_index(engine, allow_ddl=allow_ddl, rebuild=True)

    def drop_fulltext_index(self, *, allow_ddl: bool = False):
        self._require_fulltext_ddl(allow_ddl)

        def drop(cursor, state):
            self._require_bm25_metadata(state)
            spec = state.get("index")
            if spec:
                rows = self._fulltext_index_rows(spec, cursor)
                if rows:
                    if not self._fulltext_index_matches(spec, cursor, require_valid=False):
                        raise RuntimeError(
                            "Full-text index is not the expected BM25/content index; "
                            "manual cleanup is required; refusing to drop it"
                        )
                    cursor.execute(f"DROP INDEX {_quote_identifier(spec['name'])}")
            state.pop("index", None)
            state["state"] = "configured" if state.get("engine") == "bm25" else "disabled"
            return deepcopy(state)

        return self._retrieval_transaction(drop)

    def _fulltext_output_columns(self, output_fields):
        # The legacy collection helper deliberately projects NULL AS content.
        # Full-text search must return the real, migrated column instead.
        actual = set(self._get_all_columns())
        if output_fields:
            unknown = set(output_fields) - actual
            if unknown:
                raise ValueError(f"Unknown openGauss output fields: {sorted(unknown)}")
            fields = list(dict.fromkeys(["id", *output_fields]))
        else:
            fields = list(dict.fromkeys(["id", *sorted(actual)]))
        return ", ".join(_quote_identifier(field) for field in fields)

    def _bm25_page(self, cursor, sql, params, *, limit: int, offset: int):
        end = offset + limit
        if end > _BM25_MAX_CANDIDATES:
            raise RuntimeError(
                f"BM25 pagination exceeds the {_BM25_MAX_CANDIDATES} candidate safety limit"
            )
        # Native <&> cannot be evaluated by a regular Sort/Seq Scan. Keep its
        # single ordering key inside LIMIT, and sort the complete boundary tie
        # group outside. All growing prefixes must observe the same snapshot.
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        prefix = min(end + 1, _BM25_MAX_CANDIDATES)
        while True:
            # Native corpus statistics need not be MVCC-stable. Re-score the
            # whole prefix each round; never merge scores from earlier rounds.
            cursor.execute(sql, [*params, prefix])
            rows = cursor.fetchall()
            scores = [float(row[-1]) for row in rows]
            if any(not math.isfinite(score) for score in scores):
                raise ValueError("Database returned a non-finite full-text score")
            exhausted = len(rows) < prefix
            boundary_complete = len(rows) >= end and scores[-1] < scores[end - 1]
            if exhausted or boundary_complete:
                return rows[offset:end]
            if prefix == _BM25_MAX_CANDIDATES:
                raise RuntimeError(
                    "BM25 boundary tie group is not complete within the "
                    f"{_BM25_MAX_CANDIDATES} candidate safety limit"
                )
            prefix = min(prefix * 2, _BM25_MAX_CANDIDATES)

    def search_by_keywords(
        self,
        index_name: str,
        keywords: list[str] | None = None,
        query: str | None = None,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
        *,
        engine: str = "bm25",
    ) -> SearchResult:
        """Search native BM25 with a collection-wide, physically verified index.

        index_name is the ICollection logical index argument; the physical
        full-text index is collection-wide and recorded in retrieval state.
        """
        _validate_identifier(index_name, kind="logical index name")
        if engine != "bm25":
            raise ValueError("engine must be bm25")
        for name, value in (("limit", limit), ("offset", offset)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if query is not None and not isinstance(query, str):
            raise TypeError("query must be text")
        if keywords is not None and (
            not isinstance(keywords, list) or not all(isinstance(term, str) for term in keywords)
        ):
            raise TypeError("keywords must be a list of strings")
        text = query if query is not None else " ".join(keywords or [])
        with self._lock:
            self._require_bm25_metadata(self._retrieval_state())
            if not self.validate_fulltext_index(engine):
                raise RuntimeError(f"A verified, ready {engine} full-text index is required")
            if not text.strip() or limit == 0:
                return SearchResult(data=[])
            state = self._retrieval_state()
            projection = self._fulltext_output_columns(output_fields)
            where, where_params = _build_where_clause(
                self._normalize_filter_date_times(filters), self._array_fields
            )
            table = _quote_identifier(self._name)
            hint = f"/*+ indexscan ({self._name} {state['index']['name']}) */"
            predicate = f"WHERE ({where})" if where else ""
            # Neither the score predicate nor the secondary sort may enter
            # the native ordered scan. _bm25_page proves prefix completeness
            # before applying the actual page offset and limit.
            sql = (
                f"SELECT * FROM (SELECT {hint} {projection}, "
                f'"content" <&> %s AS _ov_fulltext_score FROM {table} {predicate} '
                'ORDER BY "content" <&> %s DESC LIMIT %s) AS _ov_hits '
                'WHERE _ov_fulltext_score > 0 ORDER BY _ov_fulltext_score DESC, "id"'
            )
            params = [text, *where_params, text]
            cursor = self._cursor()
            try:
                rows = self._bm25_page(cursor, sql, params, limit=limit, offset=offset)
                columns = [description[0] for description in cursor.description]
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
        items = []
        for row in rows:
            record = self._row_to_dict(row, columns)
            score = float(record.pop("_ov_fulltext_score"))
            if not math.isfinite(score):
                raise ValueError("Database returned a non-finite full-text score")
            items.append(SearchItemResult(id=record.pop("id"), fields=record, score=score))
        return SearchResult(data=items)
