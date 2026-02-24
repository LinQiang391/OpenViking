# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""System endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", tags=["system"])
async def health_check():
    """Health check endpoint (no authentication required)."""
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
async def readiness_check(request: Request):
    """Readiness check endpoint (no authentication required).

    Checks AGFS, VectorDB, and APIKeyManager connectivity.
    Returns 200 if all components are ready, 503 otherwise.
    """
    checks = {}
    all_ok = True

    # 1. AGFS connectivity
    try:
        service = get_service()
        await service._agfs_client.ls("/local")
        checks["agfs"] = "ok"
    except Exception as e:
        checks["agfs"] = f"error: {e}"
        all_ok = False
        logger.warning("Readiness check: AGFS failed: %s", e)

    # 2. VectorDB connectivity
    try:
        service = get_service()
        if service.vikingdb_manager:
            await service.vikingdb_manager.collection_exists("context")
            checks["vectordb"] = "ok"
        else:
            checks["vectordb"] = "not configured"
    except Exception as e:
        checks["vectordb"] = f"error: {e}"
        all_ok = False
        logger.warning("Readiness check: VectorDB failed: %s", e)

    # 3. APIKeyManager status
    api_key_manager = getattr(request.app.state, "api_key_manager", None)
    if api_key_manager is not None:
        checks["api_key_manager"] = "ok"
    else:
        checks["api_key_manager"] = "not configured"

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )


@router.get("/api/v1/system/status", tags=["system"])
async def system_status(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get system status."""
    service = get_service()
    return Response(
        status="ok",
        result={
            "initialized": service._initialized,
            "user": service.user._user_id,
        },
    )


class WaitRequest(BaseModel):
    """Request model for wait."""

    timeout: Optional[float] = None


@router.post("/api/v1/system/wait", tags=["system"])
async def wait_processed(
    request: WaitRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Wait for all processing to complete."""
    service = get_service()
    result = await service.resources.wait_processed(timeout=request.timeout)
    return Response(status="ok", result=result)
