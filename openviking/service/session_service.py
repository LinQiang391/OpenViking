# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Session Service for OpenViking.

Provides session management operations: session, sessions, add_message, commit, delete.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.session import Session
from openviking.session.compressor import SessionCompressor
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError, NotInitializedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext

logger = get_logger(__name__)


class SessionService:
    """Session management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        session_compressor: Optional[SessionCompressor] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_compressor = session_compressor

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        session_compressor: SessionCompressor,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_compressor = session_compressor

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    def _get_user(self, ctx: Optional["RequestContext"] = None) -> UserIdentifier:
        """Get user from ctx or default."""
        if ctx:
            return ctx.user
        return UserIdentifier.the_default_user()

    def session(
        self, session_id: Optional[str] = None, ctx: Optional["RequestContext"] = None
    ) -> Session:
        """Create a new session or load an existing one."""
        self._ensure_initialized()
        user = self._get_user(ctx)
        return Session(
            viking_fs=self._viking_fs,
            vikingdb_manager=self._vikingdb,
            session_compressor=self._session_compressor,
            user=user,
            session_id=session_id,
        )

    async def sessions(self, ctx: Optional["RequestContext"] = None) -> List[Dict[str, Any]]:
        """Get all sessions for the current user."""
        self._ensure_initialized()
        user = self._get_user(ctx)
        session_base_uri = f"viking://session/{user.user_space_name()}"

        try:
            entries = await self._viking_fs.ls(session_base_uri, ctx=ctx)
            sessions = []
            for entry in entries:
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                sessions.append(
                    {
                        "session_id": name,
                        "uri": f"{session_base_uri}/{name}",
                        "is_dir": entry.get("isDir", False),
                    }
                )
            return sessions
        except Exception:
            return []

    async def delete(self, session_id: str, ctx: Optional["RequestContext"] = None) -> bool:
        """Delete a session."""
        self._ensure_initialized()
        user = self._get_user(ctx)
        session_uri = f"viking://session/{user.user_space_name()}/{session_id}"

        try:
            await self._viking_fs.rm(session_uri, recursive=True, ctx=ctx)
            logger.info(f"Deleted session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            raise NotFoundError(session_id, "session")

    async def commit(
        self, session_id: str, ctx: Optional["RequestContext"] = None
    ) -> Dict[str, Any]:
        """Commit a session (archive messages and extract memories)."""
        self._ensure_initialized()
        session = self.session(session_id, ctx=ctx)
        await session.load()
        return session.commit()

    async def extract(self, session_id: str, ctx: Optional["RequestContext"] = None) -> List[Any]:
        """Extract memories from a session."""
        self._ensure_initialized()
        if not self._session_compressor:
            raise NotInitializedError("SessionCompressor")

        session = self.session(session_id, ctx=ctx)
        await session.load()
        user = self._get_user(ctx)

        return await self._session_compressor.extract_long_term_memories(
            messages=session.messages,
            user=user,
            session_id=session_id,
        )
