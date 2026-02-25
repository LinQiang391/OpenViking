
# Vikingbot Console Specification

## 1. Overview

Vikingbot Console 是一个轻量级的Web管理界面，用于：
- 配置 `.vikingbot/config.json`
- 查看和管理租户会话（sessions）
- 浏览工作区（workspace）内容

该服务在 `vikingbot gateway` 和 `vikingbot tui` 启动时自动启动。

## 2. Architecture

### 2.1 Tech Stack
- **Web UI**: Gradio (纯 Python 实现)
- **集成方式**: 作为 gateway/tui 的子服务启动

### 2.2 Directory Structure
```
vikingbot/
├── console/
│   ├── __init__.py
│   ├── README_GRADIO.md    # Gradio 控制台文档
│   └── console_gradio_simple.py  # Gradio 实现
```

## 3. Web UI Design

### 3.1 Pages
1. **Dashboard** - 概览页面
   - 系统状态（运行中）
   - 版本信息
   - 配置路径
   - 工作区路径

2. **Config** - 配置页面
   - **Skills & Hooks** - 独立标签页
   - **Agents** - 展开 AgentDefaults
   - **Providers** - 每个 provider 在自己的子标签页中
   - **Channels** - JSON 编辑器（可配置多个 channel）
   - **Gateway** - 网关配置
   - **Tools** - 工具配置
   - **Sandbox** - Sandbox 配置，backends 在自己的子标签页中
   - **Heartbeat** - 心跳配置
   - **Enums**: SandboxBackend, SandboxMode 使用下拉框

3. **Sessions** - 会话页面
   - 刷新按钮：加载会话列表
   - 会话选择：选择会话查看内容
   - 会话内容显示：
     - 用户消息：绿色
     - 助手消息：红色
     - 其他消息：黑色

4. **Workspace** - 工作区页面
   - Gradio 的 FileExplorer 组件
   - 显示工作区文件树
   - 选择文件查看内容

### 3.2 Design Principles
- 简洁实用
- 纯 Python，无需前端框架知识
- 无需认证（本地使用）

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
    
    # 启动 Gradio 控制台服务
    if enable_console:
        import subprocess
        import sys
        from pathlib import Path
        script_path = os.path.join(os.path.dirname(__file__), "..", "console", "console_gradio_simple.py")
        tasks.append(asyncio.create_task(
            asyncio.to_thread(
                subprocess.run,
                [sys.executable, script_path, str(console_port)],
                check=False
            )
        ))
        console.print(f"[green]✓[/green] Console: http://localhost:{console_port}")
    
    # ... 现有代码 ...
```

### 4.2 TUI Integration
在 `vikingbot/cli/commands.py` 的 `tui()` 函数中添加类似逻辑。

## 5. Security Considerations

1. **绑定地址**: 默认绑定 `0.0.0.0`，可通过防火墙限制访问
2. **认证**: 可选的基本认证（生产环境）
3. **路径限制**: workspace API 限制在工作区目录内
4. **配置备份**: 更新配置前自动备份

## 6. Implementation Phases

### Phase 1: Foundation
- 创建 Gradio 基础结构
- 集成到 gateway/tui
- Dashboard 页面

### Phase 2: Configuration UI
- 实现配置读写
- 配置验证
- 配置编辑 UI（分标签页）

### Phase 3: Sessions UI
- 实现会话列表和详情 UI
- 会话内容显示（彩色）

### Phase 4: Workspace UI
- Gradio FileExplorer 集成
- 文件内容查看

### Phase 5: Polish
- UI/UX 优化
- 错误处理
- 文档

