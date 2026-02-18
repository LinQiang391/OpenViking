# Vikingbot Console Specification

## 1. Overview

Vikingbot Console 是一个轻量级的Web管理界面，用于：
- 配置 `.vikingbot/config.json`
- 查看和管理租户会话（sessions）
- 浏览工作区（workspace）内容

该服务在 `vikingbot gateway` 和 `vikingbot tui` 启动时自动启动。

## 2. Architecture

### 2.1 Tech Stack
- **Backend**: Python + FastAPI (async)
- **Frontend**: 简单的HTML/JS
- **集成方式**: 作为 gateway/tui 的子服务启动

### 2.2 Directory Structure
```
vikingbot/
├── console/
│   ├── __init__.py
│   ├── server.py          # FastAPI app 定义
│   ├── api/
│   │   ├── __init__.py
│   │   ├── config.py      # 配置相关API
│   │   ├── sessions.py    # 会话相关API
│   │   ├── workspace.py   # 工作区相关API
│   │   └── partials.py    # 前端部分模板API
│   └── static/            # 静态文件 (HTML/CSS/JS)
│       └── index.html
```

## 3. API Design

### 3.1 Base URL
- 默认端口: `18791` (与 gateway 的 18790 区分)
- 基础路径: `/api/v1`

### 3.2 Configuration API

#### GET /api/v1/config
获取当前配置

**Response:**
```json
{
  "success": true,
  "data": {
    "providers": {...},
    "agents": {...},
    "channels": [...],
    "tools": {...},
    "sandbox": {...}
  }
}
```

#### PUT /api/v1/config
更新配置

**Request Body:**
```json
{
  "config": {
    "providers": {...},
    "agents": {...},
    "channels": [...],
    "tools": {...},
    "sandbox": {...}
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Config updated"
}
```

#### GET /api/v1/config/schema
获取配置 schema（用于前端表单生成）

**Response:**
```json
{
  "success": true,
  "data": {
    "type": "object",
    "properties": {...},
    "required": [...]
  }
}
```

#### GET /api/v1/config/path
获取配置文件路径

**Response:**
```json
{
  "success": true,
  "data": {
    "path": "/Users/user/.vikingbot/config.json"
  }
}
```

### 3.3 Sessions API

#### GET /api/v1/sessions
列出所有会话

**Query Parameters:**
- `limit`: 数量限制 (default: 50)
- `offset`: 偏移量 (default: 0)

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "key": "telegram:123456789",
      "created_at": "2024-01-15T10:30:00",
      "updated_at": "2024-01-15T14:20:00",
      "message_count": 42
    }
  ],
  "total": 100
}
```

#### GET /api/v1/sessions/{session_key}
获取单个会话详情

**Response:**
```json
{
  "success": true,
  "data": {
    "key": "telegram:123456789",
    "created_at": "2024-01-15T10:30:00",
    "updated_at": "2024-01-15T14:20:00",
    "messages": [
      {
        "role": "user",
        "content": "Hello!",
        "timestamp": "2024-01-15T10:30:01"
      },
      {
        "role": "assistant",
        "content": "Hi there!",
        "timestamp": "2024-01-15T10:30:02"
      }
    ],
    "metadata": {...}
  }
}
```

#### DELETE /api/v1/sessions/{session_key}
删除会话

**Response:**
```json
{
  "success": true,
  "message": "Session deleted"
}
```

### 3.4 Workspace API

#### GET /api/v1/workspace/files
列出工作区文件

**Query Parameters:**
- `path`: 目录路径 (default: "/")

**Response:**
```json
{
  "success": true,
  "data": {
    "path": "/",
    "files": [
      {
        "name": "AGENTS.md",
        "type": "file",
        "size": 1024,
        "modified_at": "2024-01-15T10:30:00"
      },
      {
        "name": "memory",
        "type": "directory",
        "size": 0,
        "modified_at": "2024-01-15T10:30:00"
      }
    ]
  }
}
```

#### GET /api/v1/workspace/files/{file_path}
读取文件内容

**Response:**
```json
{
  "success": true,
  "data": {
    "path": "AGENTS.md",
    "content": "# Agent Instructions\n...",
    "size": 1024,
    "modified_at": "2024-01-15T10:30:00"
  }
}
```

#### PUT /api/v1/workspace/files/{file_path}
写入文件内容

**Request Body:**
```json
{
  "content": "# New content"
}
```

**Response:**
```json
{
  "success": true,
  "message": "File written"
}
```

#### DELETE /api/v1/workspace/files/{file_path}
删除文件/目录

**Response:**
```json
{
  "success": true,
  "message": "File deleted"
}
```

### 3.5 System API

#### GET /api/v1/status
服务状态

**Response:**
```json
{
  "success": true,
  "data": {
    "version": "1.0.0",
    "uptime": 3600,
    "config_path": "/Users/user/.vikingbot/config.json",
    "workspace_path": "/Users/user/.vikingbot/workspace",
    "sessions_count": 10,
    "gateway_running": true
  }
}
```

## 4. Integration with Gateway/TUI

### 4.1 Gateway Integration
在 `vikingbot/cli/commands.py` 的 `gateway()` 函数中添加：

```python
@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    console_port: int = typer.Option(18791, "--console-port", help="Console web UI port"),
    enable_console: bool = typer.Option(True, "--console/--no-console", help="Enable console web UI"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    # ... 现有代码 ...
    
    # 启动console服务
    if enable_console:
        from vikingbot.console.server import start_console_server
        tasks.append(start_console_server(port=console_port))
        console.print(f"[green]✓[/green] Console: http://localhost:{console_port}")
    
    # ... 现有代码 ...
```

### 4.2 TUI Integration
在 `vikingbot/cli/commands.py` 的 `tui()` 函数中添加类似逻辑。

## 5. Web UI Design

### 5.1 Pages
1. **Dashboard** - 概览页面
   - 活跃会话数
   - 最近会话
   - 快捷操作

2. **Configuration** - 配置页面
   - 表单编辑配置
   - JSON 编辑器（高级模式）
   - 验证和保存

3. **Sessions** - 会话页面
   - 会话列表
   - 会话详情（消息历史）
   - 删除会话

4. **Workspace** - 工作区页面
   - 文件浏览器
   - 文件编辑器
   - 上传/下载

### 5.2 Design Principles
- 简洁实用
- 响应式设计
- 深色/浅色主题
- 无需认证（本地使用）

## 6. Security Considerations

1. **绑定地址**: 默认绑定 `127.0.0.1`，仅本地访问
2. **认证**: 可选的基本认证（生产环境）
3. **路径限制**: workspace API 限制在工作区目录内
4. **配置备份**: 更新配置前自动备份

## 7. Implementation Phases

### Phase 1: Foundation
- 创建 web 服务基础结构
- 实现 FastAPI app
- 集成到 gateway/tui

### Phase 2: Configuration API
- 实现配置读写 API
- 配置验证
- 简单的配置编辑 UI

### Phase 3: Sessions API
- 实现会话列表和详情 API
- 会话 UI

### Phase 4: Workspace API
- 实现文件浏览器 API
- 文件编辑器 UI

### Phase 5: Polish
- UI/UX 优化
- 错误处理
- 文档
