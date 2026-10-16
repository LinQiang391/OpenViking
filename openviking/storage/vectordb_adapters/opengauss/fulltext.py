# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit, collection-wide native BM25 or PostgreSQL-style FTS lifecycle."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult

from .capabilities import probe_fulltext_capabilities
from .migration import OpenGaussContentMigrationMixin
from .sql import _bounded_identifier, _build_where_clause, _quote_identifier, _validate_identifier


def _fts_expression(config: str) -> str:
    _validate_identifier(config, kind="text search configuration")
    return f"pg_catalog.to_tsvector('{config}'::regconfig, coalesce(\"content\", ''::text))"


def _canonical_expression(expression: str) -> str:
    # pg_get_indexdef varies parentheses, qualification, quotes and TEXT casts.
    expression = re.sub(r"\bCOALESCE\b", "coalesce", expression, flags=re.IGNORECASE)
    return re.sub(r'[\s()"]|::text\b|\bpg_catalog\.', "", expression)


class OpenGaussFulltextMixin(OpenGaussContentMigrationMixin):
    """Place before ICollection in collection's bases; no constructor or implicit DDL.

    Uses existing _execute/_lock/_cursor, date-filter normalization and row
    conversion. Retrieval state lives exclusively in _meta['_retrieval']; legacy
    FullText index metadata never implies a ready physical index. Each collection
    has one active full-text engine. FTS scores are ts_rank, NEVER BM25 scores.
    """

    def probe_fulltext_capabilities(self, *, text_search_config: str = "simple"):
        with self._lock:
            return probe_fulltext_capabilities(self._conn, text_search_config=text_search_config)

    def configure_fulltext(self, engine: str, text_search_config: str = "simple"):
        """Persist an explicit engine choice without changing the table or indexes."""
        if engine not in {"disabled", "bm25", "fts"}:
            raise ValueError(
                "engine must be disabled, bm25 or fts; automatic fallback is forbidden"
            )
        _validate_identifier(text_search_config, kind="text search configuration")
        with self._lock:
            capabilities = None
            if engine != "disabled":
                capabilities = self.probe_fulltext_capabilities(
                    text_search_config=text_search_config
                )
                status = capabilities[engine]["state"]
                if status != "available":
                    raise RuntimeError(f"Full-text engine {engine!r} is not available: {status}")

            def configure(cursor, state):
                unchanged = (
                    state.get("engine") == engine
                    and state.get("text_search_config") == text_search_config
                )
                state["engine"] = engine
                state["text_search_config"] = text_search_config
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
        rows = self._fulltext_index_rows(spec, cursor)
        if len(rows) != 1:
            return False
        valid, ready, method, expression, unconditional, count = rows[0]
        expected_method = "bm25" if spec["engine"] == "bm25" else "gin"
        expected_expression = (
            "content" if spec["engine"] == "bm25" else _fts_expression(spec["text_search_config"])
        )
        expression_matches = _canonical_expression(expression) == _canonical_expression(
            expected_expression
        )
        if not expression_matches and spec["engine"] == "fts":
            # A compatibility folds empty text literals to NULL in stored expressions.
            compatibility_sql = "SELECT current_setting('sql_compatibility')"
            if cursor is None:
                compatibility = self._execute(compatibility_sql, fetch=True)[0][0]
            else:
                cursor.execute(compatibility_sql)
                compatibility = cursor.fetchone()[0]
            if compatibility == "A":
                expression_matches = _canonical_expression(expression) == _canonical_expression(
                    expected_expression.replace("''::text", "NULL::text")
                )
        return bool(
            (not require_valid or (valid and ready))
            and unconditional
            and count == 1
            and method == expected_method
            and expression_matches
        )

    def validate_fulltext_index(self, engine: str | None = None) -> bool:
        """Read the real table/index catalog on every check, not legacy FullText metadata."""
        with self._lock:
            state = self._retrieval_state()
            selected = engine or state.get("engine")
            spec = state.get("index")
            if (
                state.get("state") != "ready"
                or selected not in {"bm25", "fts"}
                or selected != state.get("engine")
                or not spec
                or spec.get("engine") != selected
                or spec.get("generation") != state.get("generation")
                or spec.get("text_search_config") != state.get("text_search_config")
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
        with self._lock:
            state = self._retrieval_state()
            selected = engine or state.get("engine")
            if selected not in {"bm25", "fts"} or selected != state.get("engine"):
                raise ValueError("Configure the requested full-text engine explicitly first")
            if getattr(self, "_distributed", False):
                raise NotImplementedError(
                    "Full-text DDL on distributed collections is not verified"
                )
            if not state.get("schema_ready") or self._get_column_types().get("content") != "text":
                raise RuntimeError("Explicit content schema migration is required")
            if not rebuild and self.validate_fulltext_index(selected):
                return state
            capabilities = self.probe_fulltext_capabilities(
                text_search_config=state["text_search_config"]
            )
            if capabilities[selected]["state"] != "available":
                raise RuntimeError(f"Full-text engine {selected!r} is unavailable")
            generation = state.get("generation", 0) + 1
            spec = {
                "name": _bounded_identifier(f"{self._name}_ov_{selected}_g{generation}"),
                "engine": selected,
                "generation": generation,
                "text_search_config": state["text_search_config"],
            }
            expression = (
                '"content"' if selected == "bm25" else _fts_expression(spec["text_search_config"])
            )
            method = "bm25" if selected == "bm25" else "gin"

            def build(cursor, next_state):
                # SHARE blocks concurrent writes until the completeness check,
                # index build and metadata commit have all finished.
                cursor.execute(f"LOCK TABLE {_quote_identifier(self._name)} IN SHARE MODE")
                cursor.execute(
                    f"SELECT EXISTS (SELECT 1 FROM {_quote_identifier(self._name)} "
                    'WHERE "content" IS NULL)'
                )
                if cursor.fetchone()[0]:
                    raise RuntimeError("Content backfill is incomplete; NULL content remains")
                cursor.execute(
                    f"CREATE INDEX {_quote_identifier(spec['name'])} "
                    f"ON {_quote_identifier(self._name)} USING {method} ({expression})"
                )
                if not self._fulltext_index_matches(spec, cursor):
                    raise RuntimeError("New full-text index failed physical catalog validation")
                previous = next_state.get("index")
                if previous and previous["name"] != spec["name"]:
                    if self._fulltext_index_rows(previous, cursor):
                        if not self._fulltext_index_matches(previous, cursor, require_valid=False):
                            raise RuntimeError(
                                "Previous index identity changed; refusing to drop it"
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
            spec = state.get("index")
            if spec:
                rows = self._fulltext_index_rows(spec, cursor)
                if rows:
                    if not self._fulltext_index_matches(spec, cursor, require_valid=False):
                        raise RuntimeError("Full-text index identity changed; refusing to drop it")
                    cursor.execute(f"DROP INDEX {_quote_identifier(spec['name'])}")
            state.pop("index", None)
            state["state"] = "configured" if state.get("engine") in {"bm25", "fts"} else "disabled"
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
        """BM25 by default; callers must explicitly request engine='fts' for ts_rank.

        index_name is the ICollection logical index argument; the physical
        full-text index is collection-wide and recorded in retrieval state.
        """
        _validate_identifier(index_name, kind="logical index name")
        if engine not in {"bm25", "fts"}:
            raise ValueError("engine must be bm25 or fts")
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
            if engine == "fts":
                config = state["text_search_config"]
                vector = _fts_expression(config)
                tsquery = "pg_catalog.plainto_tsquery(%s::regconfig, %s)"
                predicate = f"{vector} @@ {tsquery}"
                if where:
                    predicate += f" AND ({where})"
                sql = (
                    f"SELECT {projection}, pg_catalog.ts_rank({vector}, {tsquery}) "
                    f"AS _ov_fulltext_score FROM {table} WHERE {predicate} "
                    'ORDER BY _ov_fulltext_score DESC, "id" LIMIT %s OFFSET %s'
                )
                params = [config, text, config, text, *where_params, limit, offset]
            else:
                hint = f"/*+ indexscan ({self._name} {state['index']['name']}) */"
                predicate = f"WHERE ({where})" if where else ""
                # Filtering positive scores outside the top-k keeps <&> out of
                # WHERE: native BM25 exposes it as an index-ordering operator.
                sql = (
                    f"SELECT * FROM (SELECT {hint} {projection}, "
                    f'"content" <&> %s AS _ov_fulltext_score FROM {table} {predicate} '
                    'ORDER BY "content" <&> %s DESC LIMIT %s OFFSET %s) AS _ov_hits '
                    'WHERE _ov_fulltext_score > 0 ORDER BY _ov_fulltext_score DESC, "id"'
                )
                params = [text, *where_params, text, limit, offset]
            cursor = self._cursor()
            try:
                cursor.execute(sql, params)
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
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
