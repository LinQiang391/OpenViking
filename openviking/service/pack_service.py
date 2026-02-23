# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Pack Service for OpenViking.

Provides ovpack export/import operations.
"""

from typing import TYPE_CHECKING, Optional

from openviking.storage.local_fs import export_ovpack as local_export_ovpack
from openviking.storage.local_fs import import_ovpack as local_import_ovpack
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext

logger = get_logger(__name__)


class PackService:
    """OVPack export/import service."""

    def __init__(self, viking_fs: Optional[VikingFS] = None):
        self._viking_fs = viking_fs

    def set_viking_fs(self, viking_fs: VikingFS) -> None:
        """Set VikingFS instance (for deferred initialization)."""
        self._viking_fs = viking_fs

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    async def export_ovpack(self, uri: str, to: str, ctx: Optional["RequestContext"] = None) -> str:
        """Export specified context path as .ovpack file."""
        viking_fs = self._ensure_initialized()
        return await local_export_ovpack(viking_fs, uri, to, ctx=ctx)

    async def import_ovpack(
        self,
        file_path: str,
        parent: str,
        force: bool = False,
        vectorize: bool = True,
        ctx: Optional["RequestContext"] = None,
    ) -> str:
        """Import local .ovpack file to specified parent path."""
        viking_fs = self._ensure_initialized()
        return await local_import_ovpack(
            viking_fs,
            file_path,
            parent,
            force=force,
            vectorize=vectorize,
            ctx=ctx,
        )
