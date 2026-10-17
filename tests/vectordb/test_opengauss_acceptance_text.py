"""Native BM25 multilingual acceptance on the explicitly isolated disposable DB.

Recall expectations cover this corpus only, not arbitrary Chinese tokenizers or
parser configurations. Removed FTS/parser options are unsupported API boundaries.
"""

import json

import pytest
from test_opengauss_acceptance_helper import acceptance_isolation  # noqa: F401
from test_opengauss_retrieval_database import _feature_sql, feature_adapter  # noqa: F401

pytestmark = pytest.mark.integration


def _prepare_text(adapter):
    collection = adapter.retrieval_collection()
    capabilities = collection.probe_fulltext_capabilities()
    assert "fts" not in capabilities
    capability = capabilities["bm25"]
    if capability["state"] != "available":
        reason = f"dedicated instance bm25 capability: {capability}"
        if capability["state"] == "unsupported":
            pytest.skip(reason)
        pytest.fail(reason, pytrace=False)
    collection.configure_fulltext("bm25")
    state = collection.migrate_content_schema(allow_ddl=True)
    assert state["schema_version"] == 2
    assert "_ov_content_empty" in collection._get_all_columns()
    texts = {
        "old": "数据库检索支持向量搜索。OpenViking retrieval transaction",
        "new": "天气预报：今天晴朗。OpenViking weather forecast",
        "private": "数据库检索。private tenant confidential",
    }
    for _ in range(4):
        result = collection.backfill_content_batch(lambda row: texts[row["id"]], batch_size=2)
        if result["done"]:
            break
    assert result["done"], "bounded multilingual backfill did not complete"
    collection.build_fulltext_index(allow_ddl=True)
    return collection


def test_multilingual_content_mutation(feature_adapter):  # noqa: F811
    adapter = feature_adapter
    _prepare_text(adapter)
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    for query, expected in [
        ("数据库", {"old"}), ("天气预报", {"new"}),
        ("OpenViking", {"old", "new"}), ("absentlexeme", set()),
    ]:
        hits = adapter.search_by_keywords(query=query, mode="bm25", filter=scope)
        assert {hit["id"] for hit in hits} == expected
    adapter.update_data([{"id": "old", "content": "replacementtoken 文本已经更新"}])
    assert adapter.search_by_keywords(query="数据库", mode="bm25", filter=scope) == []
    hits = adapter.search_by_keywords(query="replacementtoken", mode="bm25", filter=scope)
    assert [hit["id"] for hit in hits] == ["old"]
    adapter.delete(ids=["old"])
    assert adapter.search_by_keywords(query="replacementtoken", mode="bm25", filter=scope) == []


def test_chinese_native_and_adapter_recall(feature_adapter, tmp_path):  # noqa: F811
    adapter = feature_adapter
    collection = _prepare_text(adapter)
    report = {
        "engine": "bm25",
        "scope": "native BM25 recall for this corpus, not parser certification",
        "cases": [],
    }
    errors = []
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    native_sql = (
        f'SELECT * FROM (SELECT /*+ indexscan ({collection._name} '
        f'{collection._meta["_retrieval"]["index"]["name"]}) */ id, content <&> %s AS score '
        f'FROM "{collection._name}" WHERE account_id=%s '
        'ORDER BY content <&> %s DESC LIMIT 100) native_hits WHERE score>0 ORDER BY id'
    )
    for query, expected in (
        ("数据库", {"old"}), ("向量搜索", {"old"}),
        ("天气预报", {"new"}), ("今天晴朗", {"new"}),
        ("OpenViking", {"old", "new"}), ("absentlexeme", set()),
    ):
        rows = _feature_sql(adapter, native_sql, (query, "a", query))
        if "native_plan" not in report:
            report["native_plan"] = "\n".join(row[0] for row in _feature_sql(
                adapter, "EXPLAIN ANALYZE " + native_sql, (query, "a", query)
            ))
            assert collection._meta["_retrieval"]["index"]["name"] in report["native_plan"]
        native_ids = {row[0] for row in rows}
        hits = adapter.search_by_keywords(query=query, mode="bm25", filter=scope)
        adapter_ids = {hit["id"] for hit in hits}
        report["cases"].append({
            "query": query, "expected": sorted(expected), "native": sorted(native_ids),
            "adapter": sorted(adapter_ids), "native_scores": [list(row) for row in rows],
        })
        assert all(row[1] > 0 for row in rows)
        assert {hit["id"]: hit["_score"] for hit in hits} == pytest.approx(dict(rows))
        if adapter_ids != native_ids:
            errors.append(f"query defect: {query}: adapter={adapter_ids}, native={native_ids}")
        if native_ids != expected:
            errors.append(f"native engine recall limitation: {query}: native={native_ids}, expected={expected}")
    path = tmp_path / "opengauss_chinese_bm25.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nOPENGAUSS_CHINESE_DIAGNOSTIC_JSON={path}", flush=True)
    assert not errors, "; ".join(errors)


def test_removed_fts_and_parser_options_are_explicitly_unsupported(feature_adapter):  # noqa: F811
    collection = feature_adapter.retrieval_collection()
    before = collection.get_meta_data()
    with pytest.raises(ValueError, match="engine|bm25"):
        collection.configure_fulltext("fts")
    with pytest.raises(ValueError, match="mode|bm25"):
        feature_adapter.search_by_keywords(query="数据库", mode="fts")
    with pytest.raises(TypeError, match="text_search_config"):
        collection.configure_fulltext("bm25", text_search_config="ngram")
    with pytest.raises(TypeError, match="text_search_config"):
        collection.probe_fulltext_capabilities(text_search_config="zhparser")
    assert collection.get_meta_data() == before
