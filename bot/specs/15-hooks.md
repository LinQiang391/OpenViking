# Hook 机制设计

## 概述

vikingbot 的 hook 机制参考了 ClaudeCode 和 OpenCode 的设计，提供事件驱动的扩展能力，允许在系统执行的特定点插入自定义逻辑。

## 设计目标

1. **灵活性**：支持多种 hook 类型（同步、异步、函数式）
2. **类型安全**：使用 Python 类型注解确保 hook 接口规范
3. **可扩展**：易于添加新的 hook 事件和处理器类型
4. **性能优先**：异步执行，支持并行处理多个 hook
5. **向后兼容**：不影响现有代码的正常运行

## 核心架构

### 模块结构

```
vikingbot/hooks/
├── __init__.py          # 导出公共 API
├── base.py              # Hook 基类和接口定义
├── manager.py           # Hook 管理器（注册、执行）
├── events.py            # 事件类型定义
├── context.py           # Hook 上下文对象
├── registry.py          # Hook 注册表
├── sdk/                 # SDK 集成
│   └── openviking.py    # OpenViking SDK 封装
└── builtins/            # 内置 hook
    └── message_hooks.py # 消息相关 hook
```

### 类设计

#### Hook 基类 (`Hook`)

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from enum import Enum

class HookType(Enum):
    SYNC = "sync"        # 同步 hook
    ASYNC = "async"      # 异步 hook
    BLOCKING = "blocking" # 可阻塞流程的 hook

class HookContext:
    """Hook 执行上下文"""
    event_type: str
    session_key: Optional[str]
    metadata: Dict[str, Any]
    timestamp: datetime

class Hook(ABC):
    """Hook 抽象基类"""
    
    name: str
    hook_type: HookType = HookType.ASYNC
    enabled: bool = True
    
    @abstractmethod
    async def execute(self, context: HookContext, **kwargs) -> Any:
        """执行 hook 逻辑"""
        pass
    
    def should_execute(self, context: HookContext, **kwargs) -> bool:
        """判断是否应该执行此 hook（可选重写）"""
        return True
```

#### Hook 管理器 (`HookManager`)

```python
class HookManager:
    """Hook 管理器，负责注册和执行 hooks"""
    
    def __init__(self):
        self._hooks: Dict[str, List[Hook]] = defaultdict(list)
        self._registry = HookRegistry()
    
    def register(self, event_type: str, hook: Hook) -> None:
        """注册一个 hook 到指定事件"""
        self._hooks[event_type].append(hook)
    
    async def execute_hooks(
        self,
        event_type: str,
        context: HookContext,
        **kwargs
    ) -> List[Any]:
        """执行指定事件的所有 hooks"""
        
        # 筛选应该执行的 hooks
        hooks_to_execute = [
            hook for hook in self._hooks[event_type]
            if hook.enabled and hook.should_execute(context, **kwargs)
        ]
        
        # 按类型执行
        results = []
        blocking_hooks = [h for h in hooks_to_execute if h.hook_type == HookType.BLOCKING]
        async_hooks = [h for h in hooks_to_execute if h.hook_type == HookType.ASYNC]
        sync_hooks = [h for h in hooks_to_execute if h.hook_type == HookType.SYNC]
        
        # 1. 先执行 blocking hooks（串行，可中断）
        for hook in blocking_hooks:
            result = await hook.execute(context, **kwargs)
            results.append(result)
            # 如果 hook 返回了中断信号，停止执行
            if isinstance(result, dict) and result.get("block"):
                return results
        
        # 2. 并行执行 async hooks
        if async_hooks:
            async_results = await asyncio.gather(
                *[hook.execute(context, **kwargs) for hook in async_hooks]
            )
            results.extend(async_results)
        
        # 3. 执行 sync hooks
        for hook in sync_hooks:
            result = hook.execute(context, **kwargs)
            results.append(result)
        
        return results
```

## 事件定义

### 初始支持的事件

| 事件名称 | 触发时机 | 上下文参数 |
|---------|---------|-----------|
| `session.start` | 会话开始时 | session_key |
| `session.end` | 会话结束时 | session_key |
| `message.generate` | 消息生成时（用户或助理） | session_key, message |
| `message.send` | 消息发送前 | session_key, message |
| `tool.pre_execute` | 工具执行前 | session_key, tool_name, tool_input |
| `tool.post_execute` | 工具执行后 | session_key, tool_name, tool_input, tool_output |
| `agent.think` | Agent 思考阶段 | session_key, thinking_content |

### 事件上下文示例

```python
@dataclass
class MessageGenerateContext(HookContext):
    event_type = "message.generate"
    session_key: str
    message: Dict[str, Any]  # {role, content, timestamp}
    message_type: str  # "user" | "assistant"
```

## 第一个 Hook：OpenViking Add-Message

### 功能描述

在 `message.generate` 事件触发时，通过 OpenViking 的 add-message SDK 将消息写入 OpenViking 会话。

### 配置

在 `~/.vikingbot/config.json` 中添加：

```json
{
  "hooks": {
    "enabled": true,
    "openviking": {
      "api_base": "https://api.openviking.ai",
      "api_key": "your-api-key-here",
      "sync_messages": true
    }
  }
}
```

### Hook 实现

```python
# vikingbot/hooks/builtins/message_hooks.py
from typing import Any
from ..base import Hook, HookContext, HookType
from ..sdk.openviking import OpenVikingSDK
from ...config.schema import Config

class OpenVikingAddMessageHook(Hook):
    """
    通过 OpenViking SDK add-message API 写入消息
    """
    name = "openviking_add_message"
    hook_type = HookType.ASYNC
    enabled = True
    
    def __init__(self, config: Config):
        self.config = config
        self.sdk = OpenVikingSDK(
            api_base=config.hooks.openviking.api_base,
            api_key=config.hooks.openviking.api_key
        )
    
    def should_execute(self, context: HookContext, **kwargs) -> bool:
        if not self.config.hooks.enabled:
            return False
        if not self.config.hooks.openviking.sync_messages:
            return False
        return True
    
    async def execute(self, context: HookContext, **kwargs) -> Any:
        message_data = kwargs.get("message", {})
        session_key = context.session_key
        
        try:
            result = await self.sdk.add_message(
                session_id=session_key,
                role=message_data.get("role"),
                content=message_data.get("content"),
                timestamp=message_data.get("timestamp")
            )
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
```

### OpenViking SDK 封装

```python
# vikingbot/hooks/sdk/openviking.py
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime

class OpenVikingSDK:
    """OpenViking API SDK 封装"""
    
    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self.session
    
    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        调用 OpenViking add-message API
        
        参考: https://github.com/volcengine/OpenViking/blob/main/docs/zh/api/05-sessions.md
        """
        url = f"{self.api_base}/api/v1/sessions/{session_id}/messages"
        
        payload = {
            "role": role,
            "content": content,
        }
        
        if timestamp:
            payload["timestamp"] = timestamp.isoformat()
        
        session = await self._get_session()
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
```

## 集成到现有代码

### 在 Agent Loop 中集成

在 `vikingbot/agent/loop.py` 中：

```python
from vikingbot.hooks import HookManager, HookContext
from vikingbot.hooks.builtins import OpenVikingAddMessageHook

class AgentLoop:
    def __init__(self, config: Config, ...):
        ...
        # 初始化 hook 管理器
        self.hook_manager = HookManager()
        
        # 注册内置 hooks
        self._register_builtin_hooks()
    
    def _register_builtin_hooks(self):
        """注册内置 hooks"""
        if self.config.hooks and self.config.hooks.enabled:
            self.hook_manager.register(
                "message.generate",
                OpenVikingAddMessageHook(self.config)
            )
    
    async def _process_message(self, ...):
        ...
        # 在消息生成时触发 hook
        context = HookContext(
            event_type="message.generate",
            session_key=session_key,
            metadata={}
        )
        
        await self.hook_manager.execute_hooks(
            "message.generate",
            context,
            message=message_data
        )
```

### 配置 Schema 更新

在 `vikingbot/config/schema.py` 中添加：

```python
class OpenVikingHookConfig(BaseModel):
    api_base: str = "https://api.openviking.ai"
    api_key: str = ""
    sync_messages: bool = True

class HooksConfig(BaseModel):
    enabled: bool = False
    openviking: OpenVikingHookConfig = OpenVikingHookConfig()

# 在主 Config 中添加
class Config(BaseModel):
    ...
    hooks: HooksConfig = HooksConfig()
```

## 使用示例

### 启用 Hook

```bash
# 在 ~/.vikingbot/config.json 中配置
{
  "hooks": {
    "enabled": true,
    "openviking": {
      "api_base": "https://api.openviking.ai",
      "api_key": "ov_xxx",
      "sync_messages": true
    }
  }
}
```

### 自定义 Hook

```python
# my_custom_hook.py
from vikingbot.hooks import Hook, HookContext, HookType

class MyCustomHook(Hook):
    name = "my_custom_hook"
    hook_type = HookType.ASYNC
    
    async def execute(self, context: HookContext, **kwargs):
        print(f"Hook triggered: {context.event_type}")
        # 自定义逻辑
        return {"result": "success"}
```

## 未来扩展

### 计划添加的事件

- `permission.request` - 权限请求时
- `config.change` - 配置变更时
- `cron.execute` - 定时任务执行时
- `sandbox.create` - 沙箱创建时

### Hook 类型扩展

- Prompt Hook：类似 ClaudeCode，支持 LLM 决策
- Agent Hook：支持子代理执行
- Webhook Hook：支持 HTTP 回调

## 参考

- ClaudeCode Hooks: https://code.claude.com/docs/en/hooks
- OpenCode Plugins: https://opencode.ai/docs/plugins
- OpenViking Sessions API: https://github.com/volcengine/OpenViking/blob/main/docs/zh/api/05-sessions.md
