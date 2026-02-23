# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Search endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/search", tags=["search"])


class FindRequest(BaseModel):
    """Request model for find."""

    query: str
    target_uri: str = ""
    limit: int = 10
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    """Request model for search with session."""

    query: str
    target_uri: str = ""
    session_id: Optional[str] = None
    limit: int = 10
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None


class GrepRequest(BaseModel):
    """Request model for grep."""

    uri: str
    pattern: str
    case_insensitive: bool = False


class GlobRequest(BaseModel):
    """Request model for glob."""

    pattern: str
    uri: str = "viking://"


@router.post("/find")
async def find(
    request: FindRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Semantic search without session context."""
    service = get_service()
    result = await service.search.find(
        query=request.query,
        target_uri=request.target_uri,
        limit=request.limit,
        score_threshold=request.score_threshold,
        filter=request.filter,
        ctx=ctx,
    )
    # Convert FindResult to dict if it has to_dict method
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return Response(status="ok", result=result)


@router.post("/search")
async def search(
    request: SearchRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Semantic search with optional session context."""
    service = get_service()

    # Get session if session_id provided
    session = None
    if request.session_id:
        session = service.sessions.session(request.session_id, ctx=ctx)
        await session.load()

    result = await service.search.search(
        query=request.query,
        target_uri=request.target_uri,
        session=session,
        limit=request.limit,
        score_threshold=request.score_threshold,
        filter=request.filter,
        ctx=ctx,
    )
    # Convert FindResult to dict if it has to_dict method
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return Response(status="ok", result=result)


@router.post("/grep")
async def grep(
    request: GrepRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Content search with pattern."""
    service = get_service()
    result = await service.fs.grep(
        request.uri,
        request.pattern,
        case_insensitive=request.case_insensitive,
        ctx=ctx,
    )
    return Response(status="ok", result=result)


@router.post("/glob")
async def glob(
    request: GlobRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """File pattern matching."""
    service = get_service()
    result = await service.fs.glob(request.pattern, uri=request.uri, ctx=ctx)
    return Response(status="ok", result=result)
