# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit content migration and resumable, update-only backfill primitives."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from .sql import _META_TABLE, _quote_identifier


class OpenGaussContentMigrationMixin:
    """Requires collection's _conn, _cursor, RLock, _name, _meta and column helpers.

    Override _persist_retrieval_state(cursor, state) for a different metadata
    store. It MUST write using the supplied cursor without committing or changing
    in-memory metadata, and must reject stale writers. It may return merged meta.
    """

    def _retrieval_state(self) -> dict[str, Any]:
        return deepcopy(self._meta.get("_retrieval", {}))

    def _persist_retrieval_state(self, cursor, state: dict[str, Any]):
        table = _quote_identifier(_META_TABLE)
        cursor.execute(
            f"SELECT meta_json FROM {table} WHERE table_name = %s FOR UPDATE",
            (self._name,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Collection metadata is missing; retrieval state was not saved")
        stored = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        if stored.get("_retrieval", {}) != self._meta.get("_retrieval", {}):
            raise RuntimeError("Stale retrieval state; reload the collection before retrying")
        stored["_retrieval"] = deepcopy(state)
        cursor.execute(
            f"UPDATE {table} SET meta_json = %s WHERE table_name = %s",
            (json.dumps(stored), self._name),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Collection metadata update did not affect exactly one row")
        return stored

    def _retrieval_transaction(self, operation):
        with self._lock:
            state = self._retrieval_state()
            cursor = self._cursor()
            try:
                result = operation(cursor, state)
                merged = self._persist_retrieval_state(cursor, state)
                if state.get("schema_ready"):
                    if merged is None:
                        merged = deepcopy(self._meta)
                        merged["_retrieval"] = deepcopy(state)
                    fields = list(merged.get("Fields", []))
                    if not any(field.get("FieldName") == "content" for field in fields):
                        fields.append({"FieldName": "content", "FieldType": "text"})
                        merged["Fields"] = fields
                        cursor.execute(
                            f'UPDATE {_quote_identifier(_META_TABLE)} SET meta_json = %s WHERE table_name = %s',
                            (json.dumps(merged), self._name),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
            if isinstance(merged, dict):
                self._meta.update(merged)
            self._meta["_retrieval"] = deepcopy(state)
            return result

    @staticmethod
    def _require_fulltext_ddl(allow_ddl: bool):
        if allow_ddl is not True:
            raise PermissionError("Full-text DDL requires explicit allow_ddl=True")

    def migrate_content_schema(self, *, allow_ddl: bool = False) -> dict[str, Any]:
        """Add nullable TEXT content only; do not invoke loaders or language models."""
        self._require_fulltext_ddl(allow_ddl)
        with self._lock:
            types = self._get_column_types()
            if "content" in types and types["content"] != "text":
                raise ValueError("Existing content column must have type TEXT")

            def migrate(cursor, state):
                if "content" not in types:
                    cursor.execute(
                        f'ALTER TABLE {_quote_identifier(self._name)} ADD COLUMN "content" TEXT'
                    )
                state["schema_version"] = 1
                state["schema_ready"] = True
                if state.get("state") != "ready":
                    state["state"] = "schema_ready"
                return deepcopy(state)

            result = self._retrieval_transaction(migrate)
            self._field_names.add("content")
            return result

    def backfill_content_batch(
        self,
        loader: Callable[[dict[str, Any]], str],
        batch_size: int = 100,
        *,
        updated_at_field: str = "updated_at",
        source_fields: tuple[str, ...] = ("uri",),
    ) -> dict[str, Any]:
        """Load missing text from existing storage, never INSERT or generate text.

        loader(snapshot) returns str (including an explicitly empty string).
        It runs before the write transaction. A changed/deleted row aborts the
        whole batch without advancing the checkpoint; retry reloads its snapshot.
        Writers MUST bump updated_at for source changes. A bounded C-collation
        id keyset avoids OFFSET drift; late rows are covered by a subsequent pass.
        """
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if not callable(loader):
            raise TypeError("loader must be callable")
        with self._lock:
            state = self._retrieval_state()
            if not state.get("schema_ready"):
                raise RuntimeError("Run migrate_content_schema explicitly before backfill")
            columns = set(self._get_all_columns())
            fields = list(dict.fromkeys(["id", updated_at_field, *source_fields]))
            for field in fields:
                _quote_identifier(field)
                if field not in columns:
                    raise ValueError(f"Backfill source column is missing: {field}")
            table = _quote_identifier(self._name)
            checkpoint = state.get("checkpoint") or {}
            after, upper = checkpoint.get("after_id"), checkpoint.get("upper_id")
            if upper is None:
                rows = self._execute(
                    f'SELECT "id" FROM {table} ORDER BY "id" COLLATE "C" DESC LIMIT 1',
                    fetch=True,
                )
                upper = rows[0][0] if rows else None
            snapshot_fields = ", ".join(_quote_identifier(field) for field in fields)
            rows = []
            if upper is not None:
                where, params = '"content" IS NULL AND "id" COLLATE "C" <= %s', [upper]
                if after is not None:
                    where += ' AND "id" COLLATE "C" > %s'
                    params.append(after)
                rows = self._execute(
                    f"SELECT {snapshot_fields}, md5(coalesce(\"content\", '')) "
                    f'FROM {table} WHERE {where} ORDER BY "id" COLLATE "C" LIMIT %s',
                    [*params, batch_size],
                    fetch=True,
                )
            loaded = []
            for row in rows:
                snapshot = dict(zip(fields, row[:-1], strict=True))
                text = loader(deepcopy(snapshot))
                if not isinstance(text, str):
                    raise ValueError("loader must return text; missing content must not be skipped")
                loaded.append((snapshot, row[-1], text))

            def write(cursor, next_state):
                for snapshot, old_hash, text in loaded:
                    cursor.execute(
                        f'UPDATE {table} SET "content" = %s WHERE "id" = %s '
                        f"AND {_quote_identifier(updated_at_field)} IS NOT DISTINCT FROM %s "
                        'AND "content" IS NULL '
                        'AND md5(coalesce("content", \'\')) IS NOT DISTINCT FROM %s',
                        (text, snapshot["id"], snapshot[updated_at_field], old_hash),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "Backfill snapshot changed; retry batch without checkpoint advance"
                        )
                cursor.execute(f'SELECT EXISTS (SELECT 1 FROM {table} WHERE "content" IS NULL)')
                missing = bool(cursor.fetchone()[0])
                exhausted = len(rows) < batch_size
                next_state["checkpoint"] = {
                    "after_id": rows[-1][0] if rows else after,
                    "upper_id": upper,
                }
                if exhausted:
                    next_state["checkpoint"] = {"after_id": None, "upper_id": None}
                next_state["backfill_complete"] = not missing
                if missing or next_state.get("state") != "ready":
                    next_state["state"] = "backfilling" if missing else "backfilled"
                next_state["backfilled_rows"] = next_state.get("backfilled_rows", 0) + len(loaded)
                return {
                    "updated": len(loaded),
                    "done": not missing,
                    "checkpoint": deepcopy(next_state["checkpoint"]),
                }

            return self._retrieval_transaction(write)
