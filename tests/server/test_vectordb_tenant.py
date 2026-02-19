# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for VectorDB multi-tenant filtering and schema."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking_cli.retrieve.types import ContextType
from openviking_cli.session.user_id import UserIdentifier

# ---- Schema Tests ----


class TestContextCollectionSchema:
    """Verify context collection schema includes tenant fields."""

    def test_has_account_id_field(self):
        schema = CollectionSchemas.context_collection("test", 128)
        field_names = [f["FieldName"] for f in schema["Fields"]]
        assert "account_id" in field_names

    def test_has_owner_space_field(self):
        schema = CollectionSchemas.context_collection("test", 128)
        field_names = [f["FieldName"] for f in schema["Fields"]]
        assert "owner_space" in field_names

    def test_account_id_is_string(self):
        schema = CollectionSchemas.context_collection("test", 128)
        field = next(f for f in schema["Fields"] if f["FieldName"] == "account_id")
        assert field["FieldType"] == "string"

    def test_owner_space_is_string(self):
        schema = CollectionSchemas.context_collection("test", 128)
        field = next(f for f in schema["Fields"] if f["FieldName"] == "owner_space")
        assert field["FieldType"] == "string"

    def test_account_id_in_scalar_index(self):
        schema = CollectionSchemas.context_collection("test", 128)
        assert "account_id" in schema["ScalarIndex"]

    def test_owner_space_in_scalar_index(self):
        schema = CollectionSchemas.context_collection("test", 128)
        assert "owner_space" in schema["ScalarIndex"]

    def test_tenant_fields_after_id(self):
        """account_id and owner_space should be right after the id field."""
        schema = CollectionSchemas.context_collection("test", 128)
        field_names = [f["FieldName"] for f in schema["Fields"]]
        id_idx = field_names.index("id")
        assert field_names[id_idx + 1] == "account_id"
        assert field_names[id_idx + 2] == "owner_space"


# ---- Retriever Tenant Filter Tests ----


@pytest.fixture
def user_alice():
    return UserIdentifier("acme", "alice", "agent1")


@pytest.fixture
def user_bob():
    return UserIdentifier("acme", "bob", "agent2")


@pytest.fixture
def user_other():
    return UserIdentifier("other_co", "charlie", "agent1")


@pytest.fixture
def ctx_root(user_alice):
    return RequestContext(user=user_alice, role=Role.ROOT)


@pytest.fixture
def ctx_admin(user_alice):
    return RequestContext(user=user_alice, role=Role.ADMIN)


@pytest.fixture
def ctx_user_alice(user_alice):
    return RequestContext(user=user_alice, role=Role.USER)


@pytest.fixture
def ctx_user_bob(user_bob):
    return RequestContext(user=user_bob, role=Role.USER)


@pytest.fixture
def ctx_user_other(user_other):
    return RequestContext(user=user_other, role=Role.USER)


@pytest.fixture
def retriever():
    """Create a HierarchicalRetriever with no real storage/embedder."""
    from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever

    class FakeStorage:
        async def collection_exists(self, name):
            return True

    return HierarchicalRetriever(storage=FakeStorage(), embedder=None, rerank_config=None)


class TestBuildTenantFilter:
    """Test _build_tenant_filter produces correct filters per role."""

    def test_root_no_filter(self, retriever, ctx_root):
        filters = retriever._build_tenant_filter(ctx_root)
        assert filters == []

    def test_admin_filters_by_account(self, retriever, ctx_admin):
        filters = retriever._build_tenant_filter(ctx_admin)
        assert len(filters) == 1
        assert filters[0]["field"] == "account_id"
        assert filters[0]["conds"] == ["acme"]

    def test_user_filters_by_account_and_space(self, retriever, ctx_user_alice, user_alice):
        filters = retriever._build_tenant_filter(ctx_user_alice)
        assert len(filters) == 2
        # First filter: account_id
        assert filters[0]["field"] == "account_id"
        assert filters[0]["conds"] == ["acme"]
        # Second filter: owner_space
        assert filters[1]["field"] == "owner_space"
        expected_spaces = [user_alice.user_space_name(), user_alice.agent_space_name(), ""]
        assert filters[1]["conds"] == expected_spaces

    def test_different_users_different_spaces(self, retriever, ctx_user_alice, ctx_user_bob):
        filters_alice = retriever._build_tenant_filter(ctx_user_alice)
        filters_bob = retriever._build_tenant_filter(ctx_user_bob)
        # Both should have same account_id
        assert filters_alice[0]["conds"] == filters_bob[0]["conds"]  # same account
        # But different owner_space conds
        assert filters_alice[1]["conds"] != filters_bob[1]["conds"]

    def test_different_accounts_different_filters(self, retriever, ctx_user_alice, ctx_user_other):
        filters_alice = retriever._build_tenant_filter(ctx_user_alice)
        filters_other = retriever._build_tenant_filter(ctx_user_other)
        assert filters_alice[0]["conds"] != filters_other[0]["conds"]

    def test_user_filter_includes_empty_space(self, retriever, ctx_user_alice):
        """USER filter should include empty string for shared resources."""
        filters = retriever._build_tenant_filter(ctx_user_alice)
        space_filter = filters[1]
        assert "" in space_filter["conds"]


class TestGetRootUrisForType:
    """Test _get_root_uris_for_type returns correct URIs per context type and role."""

    def test_memory_with_ctx(self, retriever, ctx_user_alice, user_alice):
        uris = retriever._get_root_uris_for_type(ContextType.MEMORY, ctx=ctx_user_alice)
        assert len(uris) == 2
        assert f"viking://user/{user_alice.user_space_name()}/memories" in uris
        assert f"viking://agent/{user_alice.agent_space_name()}/memories" in uris

    def test_resource_with_ctx(self, retriever, ctx_user_alice):
        uris = retriever._get_root_uris_for_type(ContextType.RESOURCE, ctx=ctx_user_alice)
        assert uris == ["viking://resources"]

    def test_skill_with_ctx(self, retriever, ctx_user_alice, user_alice):
        uris = retriever._get_root_uris_for_type(ContextType.SKILL, ctx=ctx_user_alice)
        assert len(uris) == 1
        assert user_alice.agent_space_name() in uris[0]
        assert "skills" in uris[0]

    def test_memory_without_ctx(self, retriever):
        uris = retriever._get_root_uris_for_type(ContextType.MEMORY, ctx=None)
        assert "viking://user/memories" in uris
        assert "viking://agent/memories" in uris

    def test_resource_without_ctx(self, retriever):
        uris = retriever._get_root_uris_for_type(ContextType.RESOURCE, ctx=None)
        assert uris == ["viking://resources"]

    def test_skill_without_ctx(self, retriever):
        uris = retriever._get_root_uris_for_type(ContextType.SKILL, ctx=None)
        assert uris == ["viking://agent/skills"]

    def test_different_users_different_root_uris(self, retriever, ctx_user_alice, ctx_user_bob):
        uris_alice = retriever._get_root_uris_for_type(ContextType.MEMORY, ctx=ctx_user_alice)
        uris_bob = retriever._get_root_uris_for_type(ContextType.MEMORY, ctx=ctx_user_bob)
        assert uris_alice != uris_bob


class TestRetrieverFilterIntegration:
    """Test that retrieve() correctly integrates tenant filters."""

    def test_retrieve_merges_type_and_tenant_filters(self, retriever, ctx_user_alice):
        """Verify filter structure when both type and tenant filters are present."""

        # Build tenant filters
        tenant_filters = retriever._build_tenant_filter(ctx_user_alice)

        # Build type filter (simulating what retrieve() does)
        type_filter = {"op": "must", "field": "context_type", "conds": ["memory"]}

        filters_to_merge = [type_filter] + tenant_filters
        final_filter = {"op": "and", "conds": filters_to_merge}

        # Should have 3 conditions: type + account_id + owner_space
        assert len(final_filter["conds"]) == 3
        assert final_filter["conds"][0]["field"] == "context_type"
        assert final_filter["conds"][1]["field"] == "account_id"
        assert final_filter["conds"][2]["field"] == "owner_space"

    def test_retrieve_root_only_type_filter(self, retriever, ctx_root):
        """ROOT should only have the type filter, no tenant filters."""
        tenant_filters = retriever._build_tenant_filter(ctx_root)
        type_filter = {"op": "must", "field": "context_type", "conds": ["memory"]}

        filters_to_merge = [type_filter] + tenant_filters
        final_filter = {"op": "and", "conds": filters_to_merge}

        assert len(final_filter["conds"]) == 1
        assert final_filter["conds"][0]["field"] == "context_type"
