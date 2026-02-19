# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for storage layer multi-tenant isolation."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def viking_fs():
    """Create a VikingFS instance (no real AGFS connection needed for unit tests)."""
    return VikingFS(agfs_url="http://localhost:8080")


@pytest.fixture
def user_alice():
    return UserIdentifier("acme", "alice", "agent1")


@pytest.fixture
def user_bob():
    return UserIdentifier("acme", "bob", "agent2")


@pytest.fixture
def user_other_account():
    return UserIdentifier("other_co", "charlie", "agent1")


@pytest.fixture
def ctx_alice(user_alice):
    return RequestContext(user=user_alice, role=Role.USER)


@pytest.fixture
def ctx_bob(user_bob):
    return RequestContext(user=user_bob, role=Role.USER)


@pytest.fixture
def ctx_admin(user_alice):
    return RequestContext(user=user_alice, role=Role.ADMIN)


@pytest.fixture
def ctx_root(user_alice):
    return RequestContext(user=user_alice, role=Role.ROOT)


@pytest.fixture
def ctx_other(user_other_account):
    return RequestContext(user=user_other_account, role=Role.USER)


class TestUriToPath:
    """Test _uri_to_path with account_id prefix."""

    def test_without_account_id(self, viking_fs):
        assert viking_fs._uri_to_path("viking://resources") == "/local/resources"
        assert viking_fs._uri_to_path("viking://") == "/local"
        assert viking_fs._uri_to_path("viking://user/memories") == "/local/user/memories"

    def test_with_account_id(self, viking_fs):
        assert (
            viking_fs._uri_to_path("viking://resources", account_id="acme")
            == "/local/acme/resources"
        )
        assert viking_fs._uri_to_path("viking://", account_id="acme") == "/local/acme"
        assert (
            viking_fs._uri_to_path("viking://user/abc123/memories", account_id="acme")
            == "/local/acme/user/abc123/memories"
        )

    def test_empty_account_id(self, viking_fs):
        assert viking_fs._uri_to_path("viking://resources", account_id="") == "/local/resources"


class TestPathToUri:
    """Test _path_to_uri with account_id stripping."""

    def test_without_account_id(self, viking_fs):
        assert viking_fs._path_to_uri("/local/resources") == "viking://resources"
        assert viking_fs._path_to_uri("/local/user/memories") == "viking://user/memories"

    def test_with_account_id(self, viking_fs):
        assert (
            viking_fs._path_to_uri("/local/acme/resources", account_id="acme")
            == "viking://resources"
        )
        assert (
            viking_fs._path_to_uri("/local/acme/user/abc/memories", account_id="acme")
            == "viking://user/abc/memories"
        )
        assert viking_fs._path_to_uri("/local/acme", account_id="acme") == "viking://"

    def test_passthrough_viking_uri(self, viking_fs):
        assert (
            viking_fs._path_to_uri("viking://resources", account_id="acme") == "viking://resources"
        )


class TestExtractSpaceFromUri:
    """Test _extract_space_from_uri."""

    def test_user_space(self, viking_fs):
        assert viking_fs._extract_space_from_uri("viking://user/abc123/memories") == "abc123"

    def test_agent_space(self, viking_fs):
        assert viking_fs._extract_space_from_uri("viking://agent/def456/skills") == "def456"

    def test_session_space(self, viking_fs):
        assert viking_fs._extract_space_from_uri("viking://session/abc123/sess1") == "abc123"

    def test_resources_no_space(self, viking_fs):
        assert viking_fs._extract_space_from_uri("viking://resources") is None
        assert viking_fs._extract_space_from_uri("viking://resources/project1") is None

    def test_root_no_space(self, viking_fs):
        assert viking_fs._extract_space_from_uri("viking://") is None
        assert viking_fs._extract_space_from_uri("viking://user") is None


class TestIsAccessible:
    """Test _is_accessible for USER/ADMIN/ROOT roles."""

    def test_root_can_access_everything(self, viking_fs, ctx_root):
        assert viking_fs._is_accessible("viking://user/abc123/memories", ctx_root) is True
        assert viking_fs._is_accessible("viking://agent/xyz/skills", ctx_root) is True

    def test_admin_can_access_everything(self, viking_fs, ctx_admin):
        assert viking_fs._is_accessible("viking://user/abc123/memories", ctx_admin) is True
        assert viking_fs._is_accessible("viking://agent/xyz/skills", ctx_admin) is True

    def test_user_can_access_own_spaces(self, viking_fs, ctx_alice, user_alice):
        user_space = user_alice.user_space_name()
        agent_space = user_alice.agent_space_name()
        assert viking_fs._is_accessible(f"viking://user/{user_space}/memories", ctx_alice) is True
        assert viking_fs._is_accessible(f"viking://agent/{agent_space}/skills", ctx_alice) is True

    def test_user_cannot_access_other_spaces(self, viking_fs, ctx_alice, user_bob):
        bob_space = user_bob.user_space_name()
        assert viking_fs._is_accessible(f"viking://user/{bob_space}/memories", ctx_alice) is False

    def test_structural_dirs_accessible(self, viking_fs, ctx_alice):
        assert viking_fs._is_accessible("viking://", ctx_alice) is True
        assert viking_fs._is_accessible("viking://user", ctx_alice) is True
        assert viking_fs._is_accessible("viking://resources", ctx_alice) is True

    def test_resources_accessible_to_all(self, viking_fs, ctx_alice):
        assert viking_fs._is_accessible("viking://resources/project1", ctx_alice) is True


class TestUserIdentifierSpaces:
    """Test UserIdentifier space name methods."""

    def test_user_space_name_deterministic(self):
        u1 = UserIdentifier("acme", "alice", "agent1")
        u2 = UserIdentifier("acme", "alice", "agent2")
        # Same user_id -> same user_space_name
        assert u1.user_space_name() == u2.user_space_name()

    def test_agent_space_name_deterministic(self):
        u1 = UserIdentifier("acme", "alice", "agent1")
        u2 = UserIdentifier("acme", "alice", "agent1")
        assert u1.agent_space_name() == u2.agent_space_name()

    def test_different_users_different_spaces(self):
        u1 = UserIdentifier("acme", "alice", "agent1")
        u2 = UserIdentifier("acme", "bob", "agent1")
        assert u1.user_space_name() != u2.user_space_name()

    def test_different_agents_different_spaces(self):
        u1 = UserIdentifier("acme", "alice", "agent1")
        u2 = UserIdentifier("acme", "alice", "agent2")
        assert u1.agent_space_name() != u2.agent_space_name()

    def test_space_name_no_account_id(self):
        u = UserIdentifier("acme", "alice", "agent1")
        # user_space_name and agent_space_name should NOT contain account_id
        assert "acme" not in u.user_space_name()
        assert "acme" not in u.agent_space_name()

    def test_memory_space_uri(self):
        u = UserIdentifier("acme", "alice", "agent1")
        uri = u.memory_space_uri()
        assert uri.startswith("viking://agent/")
        assert "/memories" in uri
        assert u.agent_space_name() in uri

    def test_work_space_uri(self):
        u = UserIdentifier("acme", "alice", "agent1")
        uri = u.work_space_uri()
        assert uri.startswith("viking://agent/")
        assert "/workspaces" in uri
        assert u.agent_space_name() in uri
