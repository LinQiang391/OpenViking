# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Admin commands for multi-tenant account and user management."""

from typing import Optional

import typer

from openviking_cli.cli.context import get_cli_context
from openviking_cli.cli.errors import execute_client_command, run
from openviking_cli.cli.output import output_success

admin_app = typer.Typer(help="Account and user management commands (multi-tenant)")


# ---- Account commands ----


@admin_app.command("create-account")
def create_account_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID to create"),
    admin_user_id: str = typer.Option(..., "--admin", help="First admin user ID"),
) -> None:
    """Create a new account with its first admin user."""
    run(ctx, lambda client: client.admin_create_account(account_id, admin_user_id))


@admin_app.command("list-accounts")
def list_accounts_command(ctx: typer.Context) -> None:
    """List all accounts (ROOT only)."""
    run(ctx, lambda client: client.admin_list_accounts())


@admin_app.command("delete-account")
def delete_account_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID to delete"),
) -> None:
    """Delete an account and all associated users (ROOT only)."""
    cli_ctx = get_cli_context(ctx)
    result = execute_client_command(cli_ctx, lambda client: client.admin_delete_account(account_id))
    output_success(cli_ctx, result if result is not None else {"account_id": account_id})


# ---- User commands ----


@admin_app.command("register-user")
def register_user_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID"),
    user_id: str = typer.Argument(..., help="User ID to register"),
    role: str = typer.Option("user", "--role", help="Role: admin or user"),
) -> None:
    """Register a new user in an account."""
    run(ctx, lambda client: client.admin_register_user(account_id, user_id, role))


@admin_app.command("list-users")
def list_users_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID"),
) -> None:
    """List all users in an account."""
    run(ctx, lambda client: client.admin_list_users(account_id))


@admin_app.command("remove-user")
def remove_user_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID"),
    user_id: str = typer.Argument(..., help="User ID to remove"),
) -> None:
    """Remove a user from an account."""
    cli_ctx = get_cli_context(ctx)
    result = execute_client_command(
        cli_ctx, lambda client: client.admin_remove_user(account_id, user_id)
    )
    output_success(
        cli_ctx,
        result if result is not None else {"account_id": account_id, "user_id": user_id},
    )


@admin_app.command("set-role")
def set_role_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID"),
    user_id: str = typer.Argument(..., help="User ID"),
    role: str = typer.Argument(..., help="New role: admin or user"),
) -> None:
    """Change a user's role (ROOT only)."""
    run(ctx, lambda client: client.admin_set_role(account_id, user_id, role))


@admin_app.command("regenerate-key")
def regenerate_key_command(
    ctx: typer.Context,
    account_id: str = typer.Argument(..., help="Account ID"),
    user_id: str = typer.Argument(..., help="User ID"),
) -> None:
    """Regenerate a user's API key. Old key is immediately invalidated."""
    run(ctx, lambda client: client.admin_regenerate_key(account_id, user_id))


# ---- Invitation Token commands ----


@admin_app.command("create-invitation-token")
def create_invitation_token_command(
    ctx: typer.Context,
    max_uses: Optional[int] = typer.Option(None, "--max-uses", help="Maximum number of uses"),
    expires_at: Optional[str] = typer.Option(None, "--expires-at", help="Expiration (ISO 8601)"),
) -> None:
    """Create an invitation token for self-service registration (ROOT only)."""
    run(ctx, lambda client: client.admin_create_invitation_token(max_uses, expires_at))


@admin_app.command("list-invitation-tokens")
def list_invitation_tokens_command(ctx: typer.Context) -> None:
    """List all invitation tokens (ROOT only)."""
    run(ctx, lambda client: client.admin_list_invitation_tokens())


@admin_app.command("revoke-invitation-token")
def revoke_invitation_token_command(
    ctx: typer.Context,
    token_id: str = typer.Argument(..., help="Invitation token ID to revoke"),
) -> None:
    """Revoke an invitation token (ROOT only)."""
    cli_ctx = get_cli_context(ctx)
    result = execute_client_command(
        cli_ctx, lambda client: client.admin_revoke_invitation_token(token_id)
    )
    output_success(cli_ctx, result if result is not None else {"revoked": True})


@admin_app.command("register-account")
def register_account_command(
    ctx: typer.Context,
    invitation_token: str = typer.Option(..., "--token", help="Invitation token"),
    account_id: str = typer.Argument(..., help="Account ID to create"),
    admin_user_id: str = typer.Option(..., "--admin", help="First admin user ID"),
) -> None:
    """Register a new account using an invitation token."""
    run(ctx, lambda client: client.register_account(invitation_token, account_id, admin_user_id))


def register(app: typer.Typer) -> None:
    """Register admin command group."""
    app.add_typer(admin_app, name="admin")
