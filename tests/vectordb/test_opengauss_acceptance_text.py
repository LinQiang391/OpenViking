"""Real-engine multilingual and compatibility acceptance (disposable DSN only)."""

import pytest
from test_opengauss_retrieval_database import feature_adapter  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("engine", ["fts", "bm25"])
def test_multilingual_content_mutation(feature_adapter, engine):
    adapter = feature_adapter
    collection = adapter.retrieval_collection()
    collection.configure_fulltext(engine, text_search_config="ngram" if engine == "fts" else "simple")
    collection.migrate_content_schema(allow_ddl=True)
    texts = {
        "old": "数据库检索支持向量搜索。OpenViking retrieval transaction",
        "new": "天气预报：今天晴朗。OpenViking weather forecast",
        "private": "数据库检索。private tenant confidential",
    }
    while not collection.backfill_content_batch(lambda row: texts[row["id"]], batch_size=2)["done"]:
        pass
    collection.build_fulltext_index(allow_ddl=True)
    scope = {"op": "must", "field": "account_id", "conds": ["a"]}
    for query, expected in [("数据库", {"old"}), ("天气预报", {"new"}), ("OpenViking", {"old", "new"}), ("absentlexeme", set())]:
        hits = adapter.search_by_keywords(query=query, mode=engine, filter=scope)
        assert {hit["id"] for hit in hits} == expected
    adapter.update_data([{"id": "old", "content": "replacementtoken 文本已经更新"}])
    assert adapter.search_by_keywords(query="数据库", mode=engine, filter=scope) == []
    assert [hit["id"] for hit in adapter.search_by_keywords(query="replacementtoken", mode=engine, filter=scope)] == ["old"]
    adapter.delete(ids=["old"])
    assert adapter.search_by_keywords(query="replacementtoken", mode=engine, filter=scope) == []
