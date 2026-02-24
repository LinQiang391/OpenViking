# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""API Key management for OpenViking multi-tenant HTTP Server."""

import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from pyagfs import AGFSClient

from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import (
    AlreadyExistsError,
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

ACCOUNTS_PATH = "/local/_system/accounts.json"
USERS_PATH_TEMPLATE = "/local/{account_id}/_system/users.json"
INVITATION_TOKENS_PATH = "/local/_system/invitation_tokens.json"


@dataclass
class UserKeyEntry:
    """In-memory index entry for a user key."""

    account_id: str
    user_id: str
    role: Role


@dataclass
class AccountInfo:
    """In-memory account info."""

    created_at: str
    users: Dict[str, dict] = field(default_factory=dict)


class APIKeyManager:
    """Manages API keys for multi-tenant authentication.

    Two-level storage:
    - /_system/accounts.json: global workspace list
    - /{account_id}/_system/users.json: per-account user registry

    In-memory index for O(1) key lookup at runtime.
    """

    def __init__(self, root_key: str, agfs_url: str):
        self._root_key = root_key
        self._agfs = AGFSClient(agfs_url)
        self._accounts: Dict[str, AccountInfo] = {}
        self._user_keys: Dict[str, UserKeyEntry] = {}
        self._invitation_tokens: Dict[str, dict] = {}

    async def load(self) -> None:
        """Load accounts and user keys from AGFS into memory."""
        accounts_data = self._read_json(ACCOUNTS_PATH)
        if accounts_data is None:
            # First run: create default account
            now = datetime.now(timezone.utc).isoformat()
            accounts_data = {"accounts": {"default": {"created_at": now}}}
            self._write_json(ACCOUNTS_PATH, accounts_data)

        for account_id, info in accounts_data.get("accounts", {}).items():
            users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            users_data = self._read_json(users_path)
            users = users_data.get("users", {}) if users_data else {}

            self._accounts[account_id] = AccountInfo(
                created_at=info.get("created_at", ""),
                users=users,
            )

            for user_id, user_info in users.items():
                key = user_info.get("key", "")
                if key:
                    self._user_keys[key] = UserKeyEntry(
                        account_id=account_id,
                        user_id=user_id,
                        role=Role(user_info.get("role", "user")),
                    )

        # Load invitation tokens
        tokens_data = self._read_json(INVITATION_TOKENS_PATH)
        if tokens_data:
            self._invitation_tokens = tokens_data.get("tokens", {})

        logger.info(
            "APIKeyManager loaded: %d accounts, %d user keys, %d invitation tokens",
            len(self._accounts),
            len(self._user_keys),
            len(self._invitation_tokens),
        )

    def resolve(self, api_key: str) -> ResolvedIdentity:
        """Resolve an API key to identity. Sequential matching: root key first, then user key index."""
        if not api_key:
            raise UnauthenticatedError("Missing API Key")

        if hmac.compare_digest(api_key, self._root_key):
            return ResolvedIdentity(role=Role.ROOT)

        entry = self._user_keys.get(api_key)
        if entry:
            return ResolvedIdentity(
                role=entry.role,
                account_id=entry.account_id,
                user_id=entry.user_id,
            )

        raise UnauthenticatedError("Invalid API Key")

    async def create_account(self, account_id: str, admin_user_id: str) -> str:
        """Create a new account (workspace) with its first admin user.

        Returns the admin user's API key.
        """
        if account_id in self._accounts:
            raise AlreadyExistsError(account_id, "account")

        now = datetime.now(timezone.utc).isoformat()
        key = secrets.token_hex(32)

        self._accounts[account_id] = AccountInfo(
            created_at=now,
            users={admin_user_id: {"role": "admin", "key": key}},
        )
        self._user_keys[key] = UserKeyEntry(
            account_id=account_id,
            user_id=admin_user_id,
            role=Role.ADMIN,
        )

        self._save_accounts_json()
        self._save_users_json(account_id)
        return key

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index.

        Note: AGFS data and VectorDB cleanup is the caller's responsibility.
        """
        if account_id not in self._accounts:
            raise NotFoundError(account_id, "account")

        account = self._accounts.pop(account_id)
        for user_info in account.users.values():
            key = user_info.get("key", "")
            self._user_keys.pop(key, None)

        self._save_accounts_json()

    async def register_user(self, account_id: str, user_id: str, role: str = "user") -> str:
        """Register a new user in an account. Returns the user's API key."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id in account.users:
            raise AlreadyExistsError(user_id, "user")

        key = secrets.token_hex(32)
        account.users[user_id] = {"role": role, "key": key}
        self._user_keys[key] = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=Role(role),
        )

        self._save_users_json(account_id)
        return key

    async def remove_user(self, account_id: str, user_id: str) -> None:
        """Remove a user from an account."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        user_info = account.users.pop(user_id)
        key = user_info.get("key", "")
        self._user_keys.pop(key, None)

        self._save_users_json(account_id)

    async def regenerate_key(self, account_id: str, user_id: str) -> str:
        """Regenerate a user's API key. Old key is immediately invalidated."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        old_key = account.users[user_id].get("key", "")
        self._user_keys.pop(old_key, None)

        new_key = secrets.token_hex(32)
        account.users[user_id]["key"] = new_key
        self._user_keys[new_key] = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=Role(account.users[user_id]["role"]),
        )

        self._save_users_json(account_id)
        return new_key

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        """Update a user's role."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        account.users[user_id]["role"] = role

        key = account.users[user_id].get("key", "")
        if key in self._user_keys:
            self._user_keys[key] = UserKeyEntry(
                account_id=account_id,
                user_id=user_id,
                role=Role(role),
            )

        self._save_users_json(account_id)

    def get_accounts(self) -> list:
        """List all accounts."""
        result = []
        for account_id, info in self._accounts.items():
            result.append(
                {
                    "account_id": account_id,
                    "created_at": info.created_at,
                    "user_count": len(info.users),
                }
            )
        return result

    def get_users(self, account_id: str) -> list:
        """List all users in an account."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        result = []
        for user_id, user_info in account.users.items():
            result.append(
                {
                    "user_id": user_id,
                    "role": user_info.get("role", "user"),
                }
            )
        return result

    # ---- invitation token methods ----

    async def create_invitation_token(
        self,
        created_by: str,
        max_uses: Optional[int] = None,
        expires_at: Optional[str] = None,
    ) -> dict:
        """Create an invitation token for self-service account registration.

        Args:
            created_by: User who created this token.
            max_uses: Maximum number of uses (None = unlimited).
            expires_at: Expiration ISO timestamp (None = never expires).

        Returns:
            Token info dict.
        """
        token_id = f"inv_{secrets.token_hex(16)}"
        now = datetime.now(timezone.utc).isoformat()

        token_info = {
            "max_uses": max_uses,
            "used_count": 0,
            "expires_at": expires_at,
            "created_at": now,
            "created_by": created_by,
        }

        self._invitation_tokens[token_id] = token_info
        self._save_invitation_tokens()

        return {"token_id": token_id, **token_info}

    def list_invitation_tokens(self) -> List[dict]:
        """List all invitation tokens."""
        result = []
        for token_id, info in self._invitation_tokens.items():
            result.append({"token_id": token_id, **info})
        return result

    async def revoke_invitation_token(self, token_id: str) -> None:
        """Revoke (delete) an invitation token."""
        if token_id not in self._invitation_tokens:
            raise NotFoundError(token_id, "invitation token")

        del self._invitation_tokens[token_id]
        self._save_invitation_tokens()

    async def create_account_with_token(
        self,
        token: str,
        account_id: str,
        admin_user_id: str,
    ) -> Tuple[str, str]:
        """Create an account using an invitation token.

        Args:
            token: The invitation token ID.
            account_id: Desired account ID for the new account.
            admin_user_id: Admin user ID for the new account.

        Returns:
            Tuple of (account_id, admin_key).
        """
        token_info = self._invitation_tokens.get(token)
        if not token_info:
            raise InvalidArgumentError("Invalid invitation token")

        # Check expiration
        if token_info.get("expires_at"):
            expires = datetime.fromisoformat(token_info["expires_at"])
            if datetime.now(timezone.utc) > expires:
                raise InvalidArgumentError("Invitation token has expired")

        # Check usage limit
        if token_info.get("max_uses") is not None:
            if token_info["used_count"] >= token_info["max_uses"]:
                raise InvalidArgumentError("Invitation token has reached maximum uses")

        # Create the account
        admin_key = await self.create_account(account_id, admin_user_id)

        # Increment used_count
        token_info["used_count"] += 1
        self._save_invitation_tokens()

        return (account_id, admin_key)

    # ---- internal helpers ----

    def _read_json(self, path: str) -> Optional[dict]:
        """Read a JSON file from AGFS. Returns None if not found."""
        try:
            content = self._agfs.read(path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return json.loads(content)
        except Exception:
            return None

    def _write_json(self, path: str, data: dict) -> None:
        """Write a JSON file to AGFS, creating parent directories as needed."""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if isinstance(content, str):
            content = content.encode("utf-8")
        self._ensure_parent_dirs(path)
        self._agfs.write(path, content)

    def _ensure_parent_dirs(self, path: str) -> None:
        """Recursively create all parent directories for a file path."""
        parts = path.lstrip("/").split("/")
        for i in range(1, len(parts)):
            parent = "/" + "/".join(parts[:i])
            try:
                self._agfs.mkdir(parent)
            except Exception:
                pass

    def _save_accounts_json(self) -> None:
        """Persist the global accounts list."""
        data = {
            "accounts": {
                aid: {"created_at": info.created_at} for aid, info in self._accounts.items()
            }
        }
        self._write_json(ACCOUNTS_PATH, data)

    def _save_users_json(self, account_id: str) -> None:
        """Persist a single account's user registry."""
        account = self._accounts.get(account_id)
        if account is None:
            return
        data = {"users": account.users}
        path = USERS_PATH_TEMPLATE.format(account_id=account_id)
        self._write_json(path, data)

    def _save_invitation_tokens(self) -> None:
        """Persist invitation tokens."""
        data = {"tokens": self._invitation_tokens}
        self._write_json(INVITATION_TOKENS_PATH, data)
