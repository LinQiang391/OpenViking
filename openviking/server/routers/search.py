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
from openviking.server.trace import create_collector, inject_trace
from openviking.trace import bind_trace_collector

router = APIRouter(prefix="/api/v1/search", tags=["search"])


class FindRequest(BaseModel):
    """Request model for find."""

    query: str
    target_uri: str = ""
    limit: int = 10
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None
    trace: bool = False


class SearchRequest(BaseModel):
    """Request model for search with session."""

    query: str
    target_uri: str = ""
    session_id: Optional[str] = None
    limit: int = 10
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None
    trace: bool = False


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
    _ctx: RequestContext = Depends(get_request_context),
):
    """Semantic search without session context."""
    service = get_service()
    collector = create_collector("search.find", request.trace)
    with bind_trace_collector(collector):
        result = await service.search.find(
            query=request.query,
            ctx=_ctx,
            target_uri=request.target_uri,
            limit=request.limit,
            score_threshold=request.score_threshold,
            filter=request.filter,
        )
        # Convert FindResult to dict if it has to_dict method
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        result = inject_trace(result, collector, status="ok")
    return Response(status="ok", result=result)


@router.post("/search")
async def search(
    request: SearchRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Semantic search with optional session context."""
    service = get_service()
    collector = create_collector("search.search", request.trace)
    with bind_trace_collector(collector):
        # Get session if session_id provided
        session = None
        if request.session_id:
            session = service.sessions.session(_ctx, request.session_id)
            await session.load()

        result = await service.search.search(
            query=request.query,
            ctx=_ctx,
            target_uri=request.target_uri,
            session=session,
            limit=request.limit,
            score_threshold=request.score_threshold,
            filter=request.filter,
        )
        # Convert FindResult to dict if it has to_dict method
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        result = inject_trace(result, collector, status="ok")
    return Response(status="ok", result=result)


@router.post("/grep")
async def grep(
    request: GrepRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Content search with pattern."""
    service = get_service()
    result = await service.fs.grep(
        request.uri,
        request.pattern,
        ctx=_ctx,
        case_insensitive=request.case_insensitive,
    )
    return Response(status="ok", result=result)


@router.post("/glob")
async def glob(
    request: GlobRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """File pattern matching."""
    service = get_service()
    result = await service.fs.glob(request.pattern, ctx=_ctx, uri=request.uri)
    return Response(status="ok", result=result)
