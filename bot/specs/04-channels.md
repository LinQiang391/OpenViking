# Channels 模块设计

## 概述

Channels 模块负责与各种聊天平台的集成，提供统一的接口来接收和发送消息。

## 模块结构

```
vikingbot/channels/
├── __init__.py
├── base.py              # 通道抽象基类
├── manager.py          # 通道管理器
├── telegram.py          # Telegram 集成
├── discord.py           # Discord 集成
├── whatsapp.py          # WhatsApp 集成
├── feishu.py            # 飞书集成
├── mochat.py            # MoChat 集成
├── dingtalk.py          # 钉钉集成
├── slack.py             # Slack 集成
├── email.py             # Email 集成
└── qq.py                # QQ 集成
```

## 核心组件

### 1. BaseChannel (通道基类)

**文件**: `vikingbot/channels/base.py`

**职责**:
- 定义聊天通道的抽象接口
- 提供权限检查

**接口**:

```python
class BaseChannel(ABC):
    name: str = "base"
    
    def __init__(self, config: BaseChannelConfig, bus: MessageBus, workspace_path: Path | None = None)
    
    @abstractmethod
    async def start(self) -> None:
        """启动通道并开始监听消息"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止通道并清理资源"""
        pass
    
    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """通过此通道发送消息"""
        pass
    
    def is_allowed(self, sender_id: str) -> bool:
        """检査发送者是否被允许使用此机器人"""
        pass
    
    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """处理来自聊天平台的入站消息"""
        pass
    
    @property
    def is_running(self) -> bool:
        """检查通道是否正在运行"""
        pass
```

**权限控制**:
- `allow_from` 白名单
- 空白名单 = 允许所有人
- 非空白名单 = 仅允许列表中的用户

### 2. ChannelManager (通道管理器)

**文件**: `vikingbot/channels/manager.py`

**职责**:
- 管理多个聊天平台
- 协调消息路由

**接口**:

```python
class ChannelManager:
    def __init__(self, config: Config, bus: MessageBus)
    
    async def start_all(self) -> None
    async def stop_all(self) -> None
    def get_channel(self, name: str) -> BaseChannel | None
    def get_status(self) -> dict[str, Any]
    
    @property
    def enabled_channels(self) -> list[str]
```

**工作流程**:
1. 根据配置初始化启用的通道
2. 启动消息总线的出站分发器
3. 启动所有通道
4. 路由出站消息到对应通道

### 3. SessionKey (会话键)

**文件**: `vikingbot/config/schema.py`

**职责**:
- 唯一标识一个会话
- 替代原来的 channel/chat_id 字符串

**接口**:

```python
class SessionKey(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str
    channel_id: str
    chat_id: str

    def __hash__(self):
        return hash((self.type, self.channel_id, self.chat_id))

    def safe_name(self):
        return f'{self.type}__{self.channel_id}__{self.chat_id}'

    def channel_key(self):
        return f'{self.type}__{self.channel_id}'

    @staticmethod
    def from_safe_name(safe_name: str):
        file_name_split = safe_name.split('__')
        return SessionKey(
            type=file_name_split[0],
            channel_id=file_name_split[1],
            chat_id=file_name_split[2]
        )
```

### 4. 消息事件

**文件**: `vikingbot/bus/events.py`

**InboundMessage**:

```python
@dataclass
class InboundMessage:
    """Message received from a chat channel."""
    
    sender_id: str  # User identifier
    content: str  # Message text
    session_key: SessionKey
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
```

**OutboundMessage**:

```python
@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""
    
    session_key: SessionKey
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
```

## 支持的通道

### Telegram

**文件**: `vikingbot/channels/telegram.py`

**配置类**: `TelegramChannelConfig`

**特性**:
- Bot API 集成
- 支持 HTTP/SOCKS5 代理
- 支持文本和语音消息（Groq Whisper 转录）
- 简单设置（仅需 bot token）

**配置**:
```python
class TelegramChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.TELEGRAM
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL

    def channel_id(self) -> str:
        # Use the bot ID from token (before colon)
        return self.token.split(":")[0] if ":" in self.token else self.token
```

### Discord

**文件**: `vikingbot/channels/discord.py`

**配置类**: `DiscordChannelConfig`

**特性**:
- Bot API 集成
- 支持 MESSAGE CONTENT INTENT
- 可选的 SERVER MEMBERS INTENT
- 支持文本和媒体消息

**配置**:
```python
class DiscordChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.DISCORD
    token: str = ""  # Bot token
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT

    def channel_id(self) -> str:
        # Use first 20 chars of token as ID
        return self.token[:20]
```

### WhatsApp

**文件**: `vikingbot/channels/whatsapp.py`

**配置类**: `WhatsAppChannelConfig`

**特性**:
- 通过 Node.js bridge 连接
- 支持 WebSocket 和 HTTP polling
- 需要两个终端运行

**配置**:
```python
class WhatsAppChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.WHATSAPP
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""  # Shared token for bridge auth
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # WhatsApp typically only has one instance
        return "whatsapp"
```

### Feishu (飞书)

**文件**: `vikingbot/channels/feishu.py`

**配置类**: `FeishuChannelConfig`

**特性**:
- WebSocket 长连接（无需公网 IP）
- 支持事件订阅
- 支持消息发送

**配置**:
```python
class FeishuChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.FEISHU
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret
    encrypt_key: str = ""  # Encrypt Key (optional for Long Connection)
    verification_token: str = ""  # Verification Token (optional)
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use app_id directly as the ID
        return self.app_id

    def channel_key(self):
        return f'{self.type.value}__{self.channel_id()}'
```

### MoChat

**文件**: `vikingbot/channels/mochat.py`

**配置类**: `MochatChannelConfig`

**特性**:
- Socket.IO WebSocket 集成
- 支持 HTTP polling fallback
- 支持群组和面板
- 支持延迟回复模式

**配置**:
```python
class MochatMentionConfig(BaseModel):
    require_in_groups: bool = False

class MochatGroupRule(BaseModel):
    require_mention: bool = False

class MochatChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.MOCHAT
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000
```

### DingTalk (钉钉)

**文件**: `vikingbot/channels/dingtalk.py`

**配置类**: `DingTalkChannelConfig`

**特性**:
- Stream 模式（无需公网 IP）
- 支持消息发送

**配置**:
```python
class DingTalkChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.DINGTALK
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use client_id directly as the ID
        return self.client_id
```

### Slack

**文件**: `vikingbot/channels/slack.py`

**配置类**: `SlackChannelConfig`

**特性**:
- Socket 模式（无需公网 URL）
- 支持 DM 和群组消息
- 支持提及策略

**配置**:
```python
class SlackDMConfig(BaseModel):
    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)

class SlackChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.SLACK
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)

    def channel_id(self) -> str:
        # Use first 20 chars of bot_token as ID
        return self.bot_token[:20] if self.bot_token else "slack"
```

### Email

**文件**: `vikingbot/channels/email.py`

**配置类**: `EmailChannelConfig`

**特性**:
- IMAP 轮询接收
- SMTP 发送
- 支持自动回复控制

**配置**:
```python
class EmailChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.EMAIL
    consent_granted: bool = False  # Explicit owner permission
    
    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True
    
    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""
    
    # Behavior
    auto_reply_enabled: bool = True
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use from_address directly as the ID
        return self.from_address
```

### QQ

**文件**: `vikingbot/channels/qq.py`

**配置类**: `QQChannelConfig`

**特性**:
- botpy SDK WebSocket 集成
- 支持私聊消息
- 无需公网 IP

**配置**:
```python
class QQChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.QQ
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use app_id directly as the ID
        return self.app_id
```

## 消息流

### 入站消息流

1. 通道接收消息
2. 检查权限（`is_allowed()`）
3. 创建 `InboundMessage`（包含 `SessionKey`）
4. 发布到消息总线（`bus.publish_inbound()`）

### 出站消息流

1. Agent 生成 `OutboundMessage`（包含 `SessionKey`）
2. 发布到消息总线（`bus.publish_outbound()`）
3. ChannelManager 路由到对应通道
4. 通道调用 `send()` 发送消息

## 设计模式

### 策略模式

不同的聊天平台通过统一的 `BaseChannel` 接口互换。

### 工厂模式

`ChannelManager._init_channels()` 根据配置动态创建通道实例。

### 观察者模式

通道观察消息总线并发布消息。

## 配置

### BaseChannelConfig

```python
class BaseChannelConfig(BaseModel):
    """Base channel configuration."""
    type: ChannelType
    enabled: bool = True

    def channel_id(self) -> str:
        raise 'default'
```

### ChannelsConfig

```python
class ChannelsConfig(BaseModel):
    """Configuration for chat channels - array of channel configs."""
    channels: list[Any] = Field(default_factory=list)
    
    def _parse_channel_config(self, config: dict[str, Any]) -> BaseChannelConfig:
        """Parse a single channel config dict into the appropriate type."""
        ...
    
    def get_all_channels(self) -> list[BaseChannelConfig]:
        """Get all channel configs."""
        ...
```

## 扩展点

### 添加新通道

1. 创建通道类继承 `BaseChannel`
2. 实现所有必需的异步方法
3. 在 `ChannelManager._init_channels()` 中添加初始化代码

**示例**:

```python
class MyCustomChannel(BaseChannel):
    name = "mychannel"
    
    async def start(self) -> None:
        self._running = True
        # 连接并监听消息
        
    async def stop(self) -> None:
        self._running = False
        # 清理资源
        
    async def send(self, msg: OutboundMessage) -> None:
        # 发送消息到平台
```

## 安全考虑

### 权限控制

- 每个通道支持 `allow_from` 白名单
- 空白名单 = 允许所有人
- 非空白名单 = 仅允许列表中的用户

### API 密钥安全

- Token 存储在配置文件中
- 配置文件权限应设置为 600
- 不在日志中输出敏感信息

### 消息验证

- 通道实现 `is_allowed()` 检查
- 未授权的消息被拒绝并记录

## 性能优化

### 异步 I/O

- 所有通道方法都是异步的
- 支持并发消息处理

### 连接管理

- 自动重连机制（WebSocket）
- 心跳保持连接
- 连接超时处理

### 消息队列

- 使用异步队列缓冲消息
- 批量发送优化

---

## 多 Channel 支持

### 概述

将原来的单 channel 配置扩展为数组形式，支持配置多个同类型 channel（例如多个飞书机器人）。

### 设计目标

1. **向后兼容** - 现有配置继续工作，无需修改
2. **灵活配置** - 支持数组形式配置多个 channel
3. **会话隔离** - 不同 channel 的对话历史独立存储
4. **唯一标识** - 每个 channel 有唯一 ID，用于会话隔离

### 配置结构

#### 1. ChannelType 枚举

```python
from enum import Enum

class ChannelType(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    FEISHU = "feishu"
    MOCHAT = "mochat"
    DINGTALK = "dingtalk"
    EMAIL = "email"
    SLACK = "slack"
    QQ = "qq"
```

#### 2. BaseChannelConfig 基类

```python
class BaseChannelConfig(BaseModel):
    """基础 channel 配置"""
    type: ChannelType
    enabled: bool = True
    
    def channel_id(self) -> str:
        """获取 channel ID，子类实现"""
        raise NotImplementedError()
```

#### 3. 各 Channel 配置类

##### TelegramChannelConfig

```python
class TelegramChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.TELEGRAM
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None
    
    def channel_id(self) -> str:
        bot_id = self.token.split(":")[0] if ":" in self.token else self.token
        return bot_id
```

##### FeishuChannelConfig

```python
class FeishuChannelConfig(BaseChannelConfig):
    type: ChannelType = ChannelType.FEISHU
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    
    def channel_id(self) -> str:
        return self.app_id
```

#### 4. ChannelsConfig 更新

```python
class ChannelsConfig(BaseModel):
    """新的 channels 配置"""
    
    # 配置：数组形式
    channels: list[Union[
        TelegramChannelConfig,
        FeishuChannelConfig,
        DiscordChannelConfig,
        WhatsAppChannelConfig,
        MochatChannelConfig,
        DingTalkChannelConfig,
        EmailChannelConfig,
        SlackChannelConfig,
        QQChannelConfig,
    ]] = Field(default_factory=list)
    
    def get_all_channels(self) -> list[BaseChannelConfig]:
        """获取所有 channels"""
        result = []
        
        # 解析并返回所有 channel 配置
        for config in self.channels:
            result.append(self._parse_channel_config(config))
        
        return result
```

### 配置文件示例

#### 新格式（推荐）

```json
{
  "channels": [
    {
      "type": "feishu",
      "enabled": true,
      "app_id": "cli_xxx",
      "app_secret": "xxx"
    },
    {
      "type": "feishu",
      "enabled": true,
      "app_id": "cli_yyy",
      "app_secret": "yyy"
    },
    {
      "type": "telegram",
      "enabled": true,
      "token": "xxx"
    }
  ]
}
```

### ChannelManager 修改

#### 初始化逻辑

```python
def _init_channels(self) -> None:
    self.channels: dict[str, BaseChannel] = {}  # key = channel.channel_id()
    
    all_channel_configs = self.config.channels_config.get_all_channels()
    
    for channel_config in all_channel_configs:
        if not channel_config.enabled:
            continue
            
        channel_id = channel_config.channel_id()
        
        # 根据 type 初始化对应的 channel
        if channel_config.type == ChannelType.FEISHU:
            from vikingbot.channels.feishu import FeishuChannel
            self.channels[channel_id] = FeishuChannel(
                channel_config, 
                self.bus
            )
        elif channel_config.type == ChannelType.TELEGRAM:
            from vikingbot.channels.telegram import TelegramChannel
            self.channels[channel_id] = TelegramChannel(
                channel_config, 
                self.bus,
                groq_api_key=self.config.providers.groq.api_key,
            )
        # ... 其他类型 ...
        
        logger.info(f"Channel enabled: {channel_config.type} / {channel_id}")
```

### 各 Channel 实现修改

#### BaseChannel 更新

```python
class BaseChannel:
    def __init__(self, config: BaseChannelConfig, bus: MessageBus, workspace_path: Path | None = None):
        self.config = config
        self.bus = bus
        self._running = False
        self.channel_type = config.type
        self.channel_id = config.channel_id()
        self.workspace_path = workspace_path
```

#### SessionKey 构建

在各 channel 实现中，构建 SessionKey：

```python
# 在各 channel 实现中
session_key = SessionKey(
    type=str(self.channel_type.value),
    channel_id=self.channel_id,
    chat_id=chat_id
)
```

### Session Manager

Session Manager 现在使用 SessionKey 对象作为键，自动实现会话隔离。

### 目录结构变化

**之前**：
```
~/.vikingbot/sessions/
├── feishu:ou_xxx.jsonl
└── telegram:12345.jsonl
```

**之后**：
```
~/.vikingbot/sessions/
├── feishu__cli_xxx__ou_xxx.jsonl
├── feishu__cli_yyy__ou_yyy.jsonl
└── telegram__bot123__12345.jsonl
```

### 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `config/schema.py` | 新增 SessionKey、ChannelType、BaseChannelConfig 及各 channel 配置类，更新 ChannelsConfig |
| `channels/manager.py` | 初始化逻辑改为遍历数组，key 用 channel_id() |
| `channels/base.py` | BaseChannel 使用 BaseChannelConfig，添加 channel_id 和 channel_type |
| `channels/*.py`（各 channel） | 使用 SessionKey，通过 channel_id() 获取唯一标识 |
| `bus/events.py` | InboundMessage 和 OutboundMessage 使用 SessionKey |
| `session/manager.py` | 使用 SessionKey 作为会话键 |
| `agent/tools/base.py` | Tool 基类添加 set_session_key 方法 |
