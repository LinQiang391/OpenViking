# 认证

OpenViking Server 支持多租户 API Key 认证和基于角色的访问控制。

## 概述

OpenViking 使用两层 API Key 体系：

| Key 类型 | 创建方式 | 角色 | 用途 |
|----------|---------|------|------|
| Root Key | 服务端配置（`root_api_key`） | ROOT | 全部操作 + 管理操作 |
| User Key | Admin API | ADMIN 或 USER | 按 account 访问 |

所有 API Key 均为纯随机 token，不携带身份信息。服务端通过先比对 root key、再查 user key 索引的方式确定身份。

## 服务端配置

在 `ov.conf` 的 `server` 段配置 root API key：

```json
{
  "server": {
    "root_api_key": "your-secret-root-key"
  }
}
```

启动服务：

```bash
python -m openviking serve
```

## 管理账户和用户

使用 root key 通过 Admin API 创建工作区和用户：

```bash
# 创建工作区 + 首个 admin
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'
# 返回: {"result": {"account_id": "acme", "admin_user_id": "alice", "user_key": "..."}}

# 注册普通用户（ROOT 或 ADMIN 均可）
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "bob", "role": "user"}'
# 返回: {"result": {"account_id": "acme", "user_id": "bob", "user_key": "..."}}
```

## 客户端使用

OpenViking 支持两种方式传递 API Key：

**X-API-Key 请求头**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <user-key>"
```

**Authorization: Bearer 请求头**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "Authorization: Bearer <user-key>"
```

**Python SDK（HTTP）**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
    agent_id="my-agent"
)
```

**CLI（通过 ovcli.conf）**

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "agent_id": "my-agent"
}
```

## 角色与权限

| 角色 | 作用域 | 能力 |
|------|--------|------|
| ROOT | 全局 | 全部操作 + Admin API（创建/删除工作区、管理用户） |
| ADMIN | 所属 account | 常规操作 + 管理所属 account 的用户 |
| USER | 所属 account | 常规操作（ls、read、find、sessions 等） |

## 开发模式

不配置 `root_api_key` 时，认证禁用。所有请求以 ROOT 身份访问 default account。

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933
  }
}
```

## 邀请 Token 自助注册

除了 ROOT 手动创建账户外，OpenViking 还支持通过邀请 Token 让用户自助注册。适用于需要开放注册但又要控制入口的场景。

### 流程

```
ROOT 创建邀请 Token → 分发 Token 给用户 → 用户凭 Token 自助注册 → 获得 admin key
```

### 1. 创建邀请 Token（ROOT）

```bash
# 创建不限次数、不过期的 Token
curl -X POST http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <ROOT_KEY>" \
  -H "Content-Type: application/json" \
  -d '{}'

# 创建限 50 次使用、30 天过期的 Token
curl -X POST http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <ROOT_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"max_uses": 50, "expires_at": "2026-03-24T00:00:00Z"}'

# 返回:
# {"status": "ok", "result": {"token_id": "inv_abc123...", "account_id": "default", ...}}
```

### 2. 查看和管理 Token（ROOT）

```bash
# 列出所有邀请 Token
curl http://localhost:1933/api/v1/admin/invitation-tokens \
  -H "X-API-Key: <ROOT_KEY>"

# 撤销某个 Token
curl -X DELETE http://localhost:1933/api/v1/admin/invitation-tokens/inv_abc123 \
  -H "X-API-Key: <ROOT_KEY>"
```

### 3. 用户自助注册（无需认证）

```bash
curl -X POST http://localhost:1933/api/v1/register/account \
  -H "Content-Type: application/json" \
  -d '{
    "invitation_token": "inv_abc123...",
    "account_id": "my-team",
    "admin_user_id": "alice"
  }'

# 返回:
# {"status": "ok", "result": {"account_id": "my-team", "admin_user_id": "alice", "admin_key": "..."}}
```

注册成功后，用户获得该 account 的 admin key，可以用来注册更多用户。

### Token 限制

| 条件 | 行为 |
|------|------|
| Token 不存在或已撤销 | 注册失败 |
| Token 已过期（`expires_at`） | 注册失败 |
| Token 已达使用上限（`max_uses`） | 注册失败 |
| `account_id` 已存在 | 注册失败 |

## 无需认证的端点

以下端点不需要认证：

| 端点 | 用途 |
|------|------|
| `GET /health` | 进程存活检查 |
| `GET /ready` | 依赖就绪检查 |
| `POST /api/v1/register/account` | 邀请 Token 自助注册 |

## 相关文档

- [Admin API 参考](../api/08-admin.md) - 完整的 Admin API 参数、示例和响应格式
- [配置](01-configuration.md) - 配置文件说明
- [服务部署](03-deployment.md) - 服务部署
