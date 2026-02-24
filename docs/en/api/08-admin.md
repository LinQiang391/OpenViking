# Admin (Multi-tenant)

The Admin API manages accounts and users in a multi-tenant environment. It covers workspace (account) creation/deletion, user registration/removal, role changes, and API key regeneration.

## Roles and Permissions

| Role | Description |
|------|-------------|
| ROOT | System administrator with full access |
| ADMIN | Workspace administrator, manages users within their account |
| USER | Regular user |

| Operation | ROOT | ADMIN | USER |
|-----------|------|-------|------|
| Create/delete workspace | Y | N | N |
| List workspaces | Y | N | N |
| Register/remove users | Y | Y (own account) | N |
| Regenerate user key | Y | Y (own account) | N |
| Change user role | Y | N | N |
| Manage invitation tokens | Y | N | N |
| Self-register with token | No auth | No auth | No auth |

## API Reference

### create_account()

Create a new workspace with its first admin user.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| admin_user_id | str | Yes | - | First admin user ID |

**HTTP API**

```
POST /api/v1/admin/accounts
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

**CLI**

```bash
openviking admin create-account acme --admin alice
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "admin_user_id": "alice",
    "user_key": "7f3a9c1e..."
  },
  "time": 0.1
}
```

---

### list_accounts()

List all workspaces (ROOT only).

**HTTP API**

```
GET /api/v1/admin/accounts
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>"
```

**CLI**

```bash
openviking admin list-accounts
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {"account_id": "default", "created_at": "2026-02-12T10:00:00Z", "user_count": 1},
    {"account_id": "acme", "created_at": "2026-02-13T08:00:00Z", "user_count": 2}
  ],
  "time": 0.1
}
```

---

### delete_account()

Delete a workspace and all associated users and data (ROOT only).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID to delete |

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

**CLI**

```bash
openviking admin delete-account acme
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme"
  },
  "time": 0.1
}
```

---

### register_user()

Register a new user in a workspace.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID |
| role | str | No | "user" | Role: "admin" or "user" |

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{
    "user_id": "bob",
    "role": "user"
  }'
```

**CLI**

```bash
openviking admin register-user acme bob --role user
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "user_key": "d91f5b2a..."
  },
  "time": 0.1
}
```

---

### list_users()

List all users in a workspace.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/users
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
openviking admin list-users acme
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {"user_id": "alice", "role": "admin"},
    {"user_id": "bob", "role": "user"}
  ],
  "time": 0.1
}
```

---

### remove_user()

Remove a user from a workspace. The user's API key is deleted immediately.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID to remove |

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}/users/{user_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
openviking admin remove-user acme bob
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob"
  },
  "time": 0.1
}
```

---

### set_role()

Change a user's role (ROOT only).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID |
| role | str | Yes | - | New role: "admin" or "user" |

**HTTP API**

```
PUT /api/v1/admin/accounts/{account_id}/users/{user_id}/role
```

```bash
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "admin"}'
```

**CLI**

```bash
openviking admin set-role acme bob admin
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "role": "admin"
  },
  "time": 0.1
}
```

---

### regenerate_key()

Regenerate a user's API key. The old key is immediately invalidated.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID |

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
openviking admin regenerate-key acme bob
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "user_key": "e82d4e0f..."
  },
  "time": 0.1
}
```

---

### create_invitation_token()

Create an invitation token for self-service registration (ROOT only).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| max_uses | int | No | null | Maximum number of uses, null for unlimited |
| expires_at | str | No | null | Expiration time (ISO 8601), null for no expiry |

**HTTP API**

```
POST /api/v1/admin/invitation-tokens
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"max_uses": 50, "expires_at": "2026-03-24T00:00:00Z"}'
```

**CLI**

```bash
openviking admin create-invitation-token --max-uses 50 --expires-at "2026-03-24T00:00:00Z"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "token_id": "inv_a1b2c3d4e5f6...",
    "account_id": "default",
    "max_uses": 50,
    "used_count": 0,
    "expires_at": "2026-03-24T00:00:00Z",
    "created_at": "2026-02-24T10:00:00Z",
    "created_by": "root"
  },
  "time": 0.1
}
```

---

### list_invitation_tokens()

List all invitation tokens (ROOT only).

**HTTP API**

```
GET /api/v1/admin/invitation-tokens
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <root-key>"
```

**CLI**

```bash
openviking admin list-invitation-tokens
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {
      "token_id": "inv_a1b2c3d4e5f6...",
      "account_id": "default",
      "max_uses": 50,
      "used_count": 3,
      "expires_at": "2026-03-24T00:00:00Z",
      "created_at": "2026-02-24T10:00:00Z",
      "created_by": "root"
    }
  ],
  "time": 0.1
}
```

---

### revoke_invitation_token()

Revoke an invitation token (ROOT only). Once revoked, the token can no longer be used for registration.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| token_id | str | Yes | - | Invitation token ID |

**HTTP API**

```
DELETE /api/v1/admin/invitation-tokens/{token_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/invitation-tokens/inv_a1b2c3d4e5f6 \
  -H "X-API-Key: <root-key>"
```

**CLI**

```bash
openviking admin revoke-invitation-token inv_a1b2c3d4e5f6
```

**Response**

```json
{
  "status": "ok",
  "result": {"revoked": true},
  "time": 0.1
}
```

---

### register_account() (public)

Self-register a new account using an invitation token. This endpoint requires no authentication.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| invitation_token | str | Yes | - | Invitation token ID |
| account_id | str | Yes | - | New workspace ID |
| admin_user_id | str | Yes | - | First admin user ID |

**HTTP API**

```
POST /api/v1/register/account
```

```bash
curl -X POST http://localhost:1933/api/v1/register/account \
  -H "Content-Type: application/json" \
  -d '{
    "invitation_token": "inv_a1b2c3d4e5f6...",
    "account_id": "my-team",
    "admin_user_id": "alice"
  }'
```

**CLI**

```bash
openviking admin register-account my-team --token inv_a1b2c3d4e5f6 --admin alice
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "account_id": "my-team",
    "admin_user_id": "alice",
    "admin_key": "7f3a9c1e..."
  },
  "time": 0.1
}
```

**Error conditions**

| Condition | Error |
|-----------|-------|
| Token does not exist or was revoked | `INVALID_ARGUMENT` |
| Token has expired | `INVALID_ARGUMENT` |
| Token has reached usage limit | `INVALID_ARGUMENT` |
| `account_id` already exists | `ALREADY_EXISTS` |

---

## Full Example

### Typical Admin Workflow

```bash
# Step 1: ROOT creates workspace with alice as first admin
openviking admin create-account acme --admin alice
# Returns alice's user_key

# Step 2: alice (admin) registers regular user bob
openviking admin register-user acme bob --role user
# Returns bob's user_key

# Step 3: List all users in the account
openviking admin list-users acme

# Step 4: ROOT promotes bob to admin
openviking admin set-role acme bob admin

# Step 5: bob lost their key, regenerate (old key immediately invalidated)
openviking admin regenerate-key acme bob

# Step 6: Remove user
openviking admin remove-user acme bob

# Step 7: Delete entire workspace
openviking admin delete-account acme
```

### HTTP API Equivalent

```bash
# Step 1: Create workspace
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'

# Step 2: Register user (using alice's admin key)
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
  -d '{"user_id": "bob", "role": "user"}'

# Step 3: List users
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <alice-key>"

# Step 4: Change role (requires ROOT key)
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "admin"}'

# Step 5: Regenerate key
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>"

# Step 6: Remove user
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <alice-key>"

# Step 7: Delete workspace
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

---

## Related Documentation

- [API Overview](01-overview.md) - Authentication and response format
- [Sessions](05-sessions.md) - Session management
- [System](07-system.md) - System and monitoring API
