"""
Hook 机制集成示例

这个文件展示了如何在 AgentLoop 中集成 hook 机制。
由于直接编辑 loop.py 容易搞乱，这里提供完整的代码片段示例。
"""

from datetime import datetime


# ==========================================
# 1. 在 AgentLoop.__init__ 中添加
# ==========================================
def _init_hooks_in_agentloop(self, config):
    """
    在 AgentLoop.__init__ 中调用此方法来初始化 hook 系统

    示例:
        def __init__(self, ..., config: Config | None = None, ...):
            ...
            self._init_hooks_in_agentloop(config)
    """
    from vikingbot.hooks import HookManager
    from vikingbot.hooks.builtins import OpenVikingAddMessageHook

    self.hook_manager = HookManager()

    if config and config.hooks.enabled:
        self.hook_manager.register("message.generate", OpenVikingAddMessageHook(config))


# ==========================================
# 2. 在 _process_message 中添加 hook 触发
# ==========================================
async def _trigger_message_hooks(self, session_key, user_content, assistant_content):
    """
    在保存消息到 session 前后触发 hooks

    示例:
        # 在 session.add_message("user", ...) 之前调用
        await self._trigger_message_hooks(
            session_key=key,
            user_content=msg.content,
            assistant_content=None
        )

        session.add_message("user", msg.content)

        # 在 session.add_message("assistant", ...) 之后调用
        await self._trigger_message_hooks(
            session_key=key,
            user_content=None,
            assistant_content=final_content
        )

        session.add_message("assistant", final_content, ...)
    """
    from vikingbot.hooks import HookContext

    # 处理用户消息
    if user_content:
        user_context = HookContext(
            event_type="message.generate",
            session_key=str(session_key) if hasattr(session_key, "safe_name") else str(session_key),
        )
        await self.hook_manager.execute_hooks(
            "message.generate",
            user_context,
            message={"role": "user", "content": user_content, "timestamp": datetime.now()},
        )

    # 处理助理消息
    if assistant_content:
        assistant_context = HookContext(
            event_type="message.generate",
            session_key=str(session_key) if hasattr(session_key, "safe_name") else str(session_key),
        )
        await self.hook_manager.execute_hooks(
            "message.generate",
            assistant_context,
            message={
                "role": "assistant",
                "content": assistant_content,
                "timestamp": datetime.now(),
            },
        )


# ==========================================
# 3. 完整的集成代码片段
# ==========================================
INTEGRATION_CODE = """
# 在 AgentLoop.__init__ 中添加:

# 导入 hook 相关模块
from vikingbot.hooks import HookManager, HookContext
from vikingbot.hooks.builtins import OpenVikingAddMessageHook
from vikingbot.config.schema import Config

# 在 __init__ 方法参数中添加:
def __init__(
    self,
    ...,
    config: Config | None = None,  # 添加这个参数
    ...
):
    ...
    # 初始化 hook 管理器
    self.hook_manager = HookManager()
    
    # 注册内置 hooks
    if config and config.hooks.enabled:
        self.hook_manager.register(
            "message.generate",
            OpenVikingAddMessageHook(config)
        )
    ...


# 在 _process_message 方法中添加:
from datetime import datetime

# 在保存用户消息之前:
user_context = HookContext(
    event_type="message.generate",
    session_key=str(key) if hasattr(key, "safe_name") else str(key)
)
await self.hook_manager.execute_hooks(
    "message.generate",
    user_context,
    message={
        "role": "user",
        "content": msg.content,
        "timestamp": datetime.now()
    }
)

session.add_message("user", msg.content)

# 在保存助理消息之后:
assistant_context = HookContext(
    event_type="message.generate",
    session_key=str(key) if hasattr(key, "safe_name") else str(key)
)
await self.hook_manager.execute_hooks(
    "message.generate",
    assistant_context,
    message={
        "role": "assistant",
        "content": final_content,
        "timestamp": datetime.now()
    }
)

session.add_message("assistant", final_content, tools_used=tools_used if tools_used else None)
"""


# ==========================================
# 4. 配置示例
# ==========================================
CONFIG_EXAMPLE = """
# 在 ~/.vikingbot/config.json 中添加:

{
  "hooks": {
    "enabled": true,
    "openviking": {
      "api_base": "http://localhost:1933",
      "api_key": "your-api-key-here",
      "sync_messages": true
    }
  }
}
"""


if __name__ == "__main__":
    print("=" * 80)
    print("Hook 机制集成示例")
    print("=" * 80)
    print()
    print("1. 初始化 Hook 系统:")
    print("-" * 80)
    print("   在 AgentLoop.__init__ 中添加初始化代码")
    print()
    print("2. 触发 Hook 事件:")
    print("-" * 80)
    print("   在 _process_message 中保存消息前后触发 hooks")
    print()
    print("3. 配置文件:")
    print("-" * 80)
    print("   在 config.json 中启用 hooks 并配置 OpenViking")
    print()
    print("=" * 80)
    print("完整代码见本文件中的 INTEGRATION_CODE 变量")
    print("=" * 80)
