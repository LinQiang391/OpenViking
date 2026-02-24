# Authentication

OpenViking Server supports multi-tenant API key authentication with role-based access control.

## Overview

OpenViking uses a two-layer API key system:

| Key Type | Created By | Role | Purpose |
|----------|-----------|------|---------|
| Root Key | Server config (`root_api_key`) | ROOT | Full access + admin operations |
| User Key | Admin API | ADMIN or USER | Per-account access |

All API keys are plain random tokens with no embedded identity. The server resolves identity by first comparing against the root key, then looking up the user key index.

## Setting Up (Server Side)

Configure the root API key in the `server` section of `ov.conf`:

```json
{
  "server": {
    "root_api_key": "your-secret-root-key"
  }
}
```

Start the server:

```bash
python -m openviking serve
```

## Managing Accounts and Users

Use the root key to create accounts (workspaces) and users via the Admin API:

```bash
# Create account with first admin
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'
# Returns: {"result": {"account_id": "acme", "admin_user_id": "alice", "user_key": "..."}}

# Register a regular user (as ROOT or ADMIN)
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "bob", "role": "user"}'
# Returns: {"result": {"account_id": "acme", "user_id": "bob", "user_key": "..."}}
```

## Using API Keys (Client Side)

OpenViking accepts API keys via two headers:

**X-API-Key header**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <user-key>"
```

**Authorization: Bearer header**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "Authorization: Bearer <user-key>"
```

**Python SDK (HTTP)**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
    agent_id="my-agent"
)
```

**CLI (via ovcli.conf)**

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "agent_id": "my-agent"
}
```

## Roles and Permissions

| Role | Scope | Capabilities |
|------|-------|-------------|
| ROOT | Global | All operations + Admin API (create/delete accounts, manage users) |
| ADMIN | Own account | Regular operations + manage users in own account |
| USER | Own account | Regular operations (ls, read, find, sessions, etc.) |

## Development Mode

When no `root_api_key` is configured, authentication is disabled. All requests are accepted as ROOT with the default account.

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933
  }
}
```

## Invitation Token Self-Registration

In addition to ROOT manually creating accounts, OpenViking supports self-service registration via invitation tokens. This is useful for controlled open registration.

### Flow

```
ROOT creates invitation token → distributes token → user self-registers → gets admin key
```

### 1. Create Invitation Token (ROOT)

```bash
# Create unlimited, non-expiring token
curl -X POST http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <ROOT_KEY>" \
  -H "Content-Type: application/json" \
  -d '{}'

# Create token with 50 uses limit, 30-day expiry
curl -X POST http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <ROOT_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"max_uses": 50, "expires_at": "2026-03-24T00:00:00Z"}'

# Returns:
# {"status": "ok", "result": {"token_id": "inv_abc123...", "account_id": "default", ...}}
```

### 2. Manage Tokens (ROOT)

```bash
# List all invitation tokens
curl http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <ROOT_KEY>"

# Revoke a token
curl -X DELETE http://localhost:1933/api/v1/admin/invitation-tokens/inv_abc123 \
  -H "X-API-Key: <ROOT_KEY>"
```

### 3. Self-Register (No Auth Required)

```bash
curl -X POST http://localhost:1933/api/v1/register/account \
  -H "Content-Type: application/json" \
  -d '{
    "invitation_token": "inv_abc123...",
    "account_id": "my-team",
    "admin_user_id": "alice"
  }'

# Returns:
# {"status": "ok", "result": {"account_id": "my-team", "admin_user_id": "alice", "admin_key": "..."}}
```

After registration, the user receives an admin key for the new account and can register additional users.

### Token Constraints

| Condition | Behavior |
|-----------|----------|
| Token does not exist or was revoked | Registration fails |
| Token has expired (`expires_at`) | Registration fails |
| Token has reached usage limit (`max_uses`) | Registration fails |
| `account_id` already exists | Registration fails |

## Unauthenticated Endpoints

The following endpoints do not require authentication:

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Process liveness check |
| `GET /ready` | Dependency readiness check |
| `POST /api/v1/register/account` | Invitation token self-registration |

## Related Documentation

- [Admin API Reference](../api/08-admin.md) - Full Admin API parameters, examples, and response formats
- [Configuration](01-configuration.md) - Config file reference
- [Deployment](03-deployment.md) - Server setup
