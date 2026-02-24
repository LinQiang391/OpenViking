# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Public registration endpoint for OpenViking HTTP Server."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from openviking.server.models import Response
from openviking_cli.exceptions import PermissionDeniedError

router = APIRouter(prefix="/api/v1/register", tags=["register"])


class RegisterAccountRequest(BaseModel):
    invitation_token: str
    account_id: str
    admin_user_id: str


@router.post("/account")
async def register_account(body: RegisterAccountRequest, request: Request):
    """Register a new account using an invitation token (no authentication required)."""
    manager = getattr(request.app.state, "api_key_manager", None)
    if manager is None:
        raise PermissionDeniedError("Registration requires API key management to be configured")

    account_id, admin_key = await manager.create_account_with_token(
        token=body.invitation_token,
        account_id=body.account_id,
        admin_user_id=body.admin_user_id,
    )
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            "admin_user_id": body.admin_user_id,
            "admin_key": admin_key,
        },
    )
