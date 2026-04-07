# OpenClaw-Plugin assemble() 流程梳理

## 一、assemble 是什么

`assemble()` 是 OpenClaw 的 **Context Engine 标准接口**之一。当 agent 准备构建 prompt 时，OpenClaw SDK 会调用 Context Engine 的 `assemble()` 方法，让引擎有机会：

1. **替换**输入的原始 messages（用更高效的压缩版本）
2. **注入**系统提示补充（systemPromptAddition）
3. **估算** token 数

简单说：`assemble()` 负责把 OpenViking 服务器上的「归档摘要 + 归档索引 + 活跃消息」**组装**成 agent 可以直接使用的 messages 数组。

## 二、调用时机

```
用户发送消息
  ↓
OpenClaw SDK → before_prompt_build hooks (auto-recall 等)
  ↓
OpenClaw SDK → contextEngine.assemble({ sessionId, messages, tokenBudget, ... })
  ↓
组装结果 → SDK 构建最终 prompt → 发给 LLM
```

`assemble()` 在 `before_prompt_build` 之后、实际 LLM 调用之前执行。

## 三、输入参数

```typescript
assemble(params: {
  sessionId: string;          // OpenClaw 的 session UUID
  sessionKey?: string;        // 稳定的逻辑标识符，如 "agent:main:main"
  messages: AgentMessage[];   // SDK 传入的原始对话消息（当前 session 的 live messages）
  tokenBudget?: number;       // token 预算上限，默认 128,000
  runtimeContext?: Record<string, unknown>;  // 运行时上下文（可能包含 sessionKey、agentId）
})
```

## 四、完整流程图

```
assemble(params)
  │
  ├─ 1. 提取 sessionKey、计算原始 token 估算
  │
  ├─ 2. Session 过滤检查
  │     └─ maybeSkipIgnoredSession("assemble", ...)
  │        命中 → 直接返回 { messages: 原始messages, estimatedTokens }
  │
  ├─ 3. Session ID 转换
  │     └─ openClawSessionToOvStorageId(sessionId, sessionKey)
  │        → OV 存储层可用的 session ID（UUID 或 SHA256）
  │
  ├─ 4. 记录 agentId 映射 + 诊断日志
  │
  ├─ 5. 本地模式预检查（仅 mode=local）
  │     └─ quickPrecheck() → 失败则 fallback 到原始 messages
  │
  ├─ 6. 调用 OpenViking API
  │     └─ client.getSessionContext(OVSessionId, tokenBudget, agentId)
  │        → GET /api/v1/sessions/{id}/context?token_budget={budget}
  │
  ├─ 7. 判断是否有有效数据
  │     ├─ 无归档 + 无活跃消息 → fallback（新用户首次对话）
  │     └─ 无归档 + OV 活跃消息数 < SDK 原始消息数 → fallback（OV 数据看起来被截断）
  │
  ├─ 8. 组装消息数组
  │     ├─ 8a. [Session History Summary]  ← 归档概览
  │     ├─ 8b. [Archive Index]            ← 各归档摘要列表
  │     └─ 8c. 活跃消息                    ← OV parts 格式转 OpenClaw AgentMessage 格式
  │
  ├─ 9. 后处理
  │     ├─ normalizeAssistantContent()     ← assistant string → content blocks
  │     └─ sanitizeToolUseResultPairing()  ← 修复 toolUse/toolResult 配对
  │
  ├─ 10. 空结果兜底
  │     └─ 组装后为空但原始不为空 → fallback 到原始 messages
  │
  └─ 11. 返回结果
        ├─ messages: 组装后的消息数组
        ├─ estimatedTokens: OV 返回的 token 估算
        └─ systemPromptAddition: Session Context Guide（仅有归档时才注入）
```

## 五、各步骤详解

### 5.1 Session ID 转换

OpenClaw 使用 `sessionId`（UUID）和 `sessionKey`（逻辑标识符如 `agent:main:main`）标识会话。
OpenViking 使用自己的 session ID（必须是文件系统安全的路径段）。

转换规则（`openClawSessionToOvStorageId`）：

```
sessionId 是标准 UUID？  → 直接使用（小写）
有 sessionKey？          → SHA256(sessionKey)
sessionId 含 Windows 非法字符？ → SHA256("openclaw-session:" + sessionId)
都不是？                 → 直接使用 sessionId
```

### 5.2 调用 OpenViking API

```
GET /api/v1/sessions/{OVSessionId}/context?token_budget={budget}
```

返回结构（`SessionContextResult`）：

```typescript
{
  latest_archive_overview: string;       // 所有归档的综合概览摘要
  pre_archive_abstracts: [               // 各归档条目列表
    { archive_id: "archive_001", abstract: "之前讨论了仓库搭建" },
    { archive_id: "archive_002", abstract: "实现了用户认证" },
  ];
  messages: OVMessage[];                 // 当前活跃（未归档）的消息
  estimatedTokens: number;               // 服务端估算的总 token 数
  stats: {
    totalArchives: number;
    includedArchives: number;
    droppedArchives: number;             // token budget 不够时会丢弃旧归档
    failedArchives: number;
    activeTokens: number;
    archiveTokens: number;
  };
}
```

### 5.3 消息组装

组装结果的消息顺序：

```
┌─────────────────────────────────────────────────────────┐
│ [1] user: "[Session History Summary]\n# Session..."     │  ← 归档概览
│ [2] user: "[Archive Index]\narchive_001: ...\n..."      │  ← 归档索引
│ [3] assistant: { content: [text, text, toolUse, ...] }  │  ← OV 活跃消息(转换后)
│ [4] toolResult: { toolCallId: "...", content: [...] }   │  ← 对应的工具结果
│ [5] user: "用户的最新消息..."                              │  ← OV 活跃消息(转换后)
│ ... (更多 OV 活跃消息)                                     │
└─────────────────────────────────────────────────────────┘
```

### 5.4 OV Message → AgentMessage 转换

OpenViking 存储的消息格式（parts-based）与 OpenClaw 的 AgentMessage 格式不同，需要转换。

**OV 消息格式**：

```typescript
{
  id: "msg_1",
  role: "assistant",
  parts: [
    { type: "text", text: "检查了最新上下文。" },
    { type: "context", abstract: "用户偏好简洁回答。" },
    { type: "tool", tool_id: "tool_123", tool_name: "read_file",
      tool_input: { path: "src/app.ts" },
      tool_output: "export const value = 1;",
      tool_status: "completed" }
  ]
}
```

**转换后的 AgentMessage 格式**：

```typescript
// 一条 OV assistant 消息 → 拆分为多条 AgentMessage

// [1] assistant 消息（包含 text + toolUse blocks）
{
  role: "assistant",
  content: [
    { type: "text", text: "检查了最新上下文。" },
    { type: "text", text: "用户偏好简洁回答。" },           // context.abstract → text
    { type: "toolUse", id: "tool_123", name: "read_file",
      input: { path: "src/app.ts" } }
  ]
}

// [2] toolResult 消息（每个 tool part 生成一条）
{
  role: "toolResult",
  toolCallId: "tool_123",
  toolName: "read_file",
  content: [{ type: "text", text: "export const value = 1;" }],
  isError: false
}
```

**转换规则**（`convertToAgentMessages`）：

| OV Part 类型 | 转换结果 |
|-------------|---------|
| `text` | → `{ type: "text", text: ... }` 放入 assistant content |
| `context` | → abstract 转为 `{ type: "text", text: abstract }` |
| `tool`（有 tool_id） | → assistant content 中添加 `toolUse` block + 单独生成 `toolResult` 消息 |
| `tool`（无 tool_id） | → 降级为 text block（保留信息但不生成 toolUse/toolResult） |

**tool_status 影响 toolResult 内容**：

| tool_status | toolResult 行为 |
|-------------|----------------|
| `completed` | `content = tool_output`, `isError = false` |
| `error` | `content = tool_output`, `isError = true` |
| 其他（running 等） | `content = "(interrupted — tool did not complete)"`, `isError = false` |

**user 消息的转换**更简单：所有 text parts 合并为一个字符串。

### 5.5 后处理

#### normalizeAssistantContent

确保所有 assistant 消息的 content 都是 blocks 数组格式：

```typescript
// 转换前（string 格式）
{ role: "assistant", content: "Hello" }

// 转换后（blocks 格式）
{ role: "assistant", content: [{ type: "text", text: "Hello" }] }
```

这一步是因为 OV 的 user 消息转换后可能是 string，但 assistant 消息必须是 content blocks 数组（Anthropic API 要求）。

#### sanitizeToolUseResultPairing

修复 toolUse 和 toolResult 的配对问题。OpenViking 的归档/恢复过程中可能出现：

1. **toolResult 位移** — 结果消息跑到了 user 消息后面
2. **toolResult 缺失** — 工具中断，没有结果记录
3. **toolResult 重复** — 相同 ID 出现多次

修复策略：
- 将 toolResult 移动到对应 toolUse 所在 assistant 消息的紧后方
- 为缺失的 toolResult 插入合成的 error 结果
- 去除重复的 toolResult

### 5.6 systemPromptAddition

当存在归档数据时，assemble 会额外返回一段 system prompt 补充内容（`buildSystemPromptAddition`），指导 agent 如何使用组装后的上下文：

```
## Session Context Guide

Your conversation history may include:

1. **[Session History Summary]** — 所有先前会话的压缩摘要...
2. **[Archive Index]** — 归档列表（按时间排序，archive_001 最旧）...
3. **Active messages** — 当前未压缩的对话...

**When you need precise details from a prior session:**
1. 查看 Archive Index 定位相关归档
2. 调用 ov_archive_expand 获取原始消息
3. 多个归档相关时，优先展开最近的
4. 结合展开内容和活跃消息回答

**Rules:**
- 活跃消息和归档冲突时，信任活跃消息
- 只在需要具体细节时才展开归档
- 不要从摘要中编造细节
- 展开后在回答中引用归档 ID
```

## 六、Fallback 策略

assemble 有多个 fallback 点，确保在任何故障下都能正常工作：

| 场景 | Fallback 行为 | 原因 |
|------|-------------|------|
| Session 被 ignoreSessionPatterns 匹配 | 返回原始 messages | 不应参与上下文管理 |
| Local 模式预检查失败 | 返回原始 messages | OV 服务不可用 |
| OV API 调用异常 | 返回原始 messages | 网络/服务故障 |
| OV 返回空数据（无归档 + 无活跃消息） | 返回原始 messages | 新用户首次对话 |
| OV 活跃消息数 < SDK 原始消息数（且无归档） | 返回原始 messages | OV 数据可能不完整 |
| 组装+后处理后为空 | 返回原始 messages | 转换过程丢失了所有内容 |

**关键设计原则**：assemble 的 fallback 永远是「返回 SDK 传入的原始 messages」，永远不会让 agent 看到一个空的或损坏的对话历史。

## 七、数据流全景图

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  OpenClaw    │     │  openclaw-   │     │   OpenViking     │
│    SDK       │     │  plugin      │     │    Server        │
└──────┬───────┘     └──────┬───────┘     └────────┬─────────┘
       │                    │                      │
       │  assemble(params)  │                      │
       │───────────────────>│                      │
       │                    │                      │
       │                    │  session 过滤检查     │
       │                    │  ├─ 命中 → 直接返回   │
       │                    │  └─ 通过 ↓           │
       │                    │                      │
       │                    │  sessionId 转换       │
       │                    │  ↓                   │
       │                    │  GET /context         │
       │                    │─────────────────────>│
       │                    │                      │
       │                    │  SessionContextResult │
       │                    │<─────────────────────│
       │                    │                      │
       │                    │  组装消息：            │
       │                    │  ├─ History Summary   │
       │                    │  ├─ Archive Index     │
       │                    │  └─ Active Messages   │
       │                    │    (OV→AgentMessage)  │
       │                    │                      │
       │                    │  后处理：              │
       │                    │  ├─ normalize         │
       │                    │  └─ sanitize pairing  │
       │                    │                      │
       │  AssembleResult    │                      │
       │<───────────────────│                      │
       │  {messages,        │                      │
       │   estimatedTokens, │                      │
       │   systemPrompt}    │                      │
```

## 八、与 afterTurn 的配合关系

`assemble` 和 `afterTurn` 是一对互补操作：

| | assemble（读取） | afterTurn（写入） |
|---|---|---|
| **方向** | OV → Agent | Agent → OV |
| **触发时机** | 构建 prompt 前 | LLM 响应后 |
| **操作** | 从 OV 获取归档+活跃消息，替换 SDK messages | 提取新轮次文本，写入 OV session |
| **归档** | 读取已归档内容 | 触发 commit（可能产生新归档） |

```
[对话轮次 N]
  assemble → 从 OV 读取上下文 → agent 处理 → afterTurn → 新消息写入 OV

[对话轮次 N+1]
  assemble → 读取更新后的 OV 上下文（可能含新归档）→ ...
```

## 九、关键代码位置

| 功能 | 文件 | 行号 |
|------|------|------|
| assemble 主函数 | `context-engine.ts` | L641-788 |
| OV parts → AgentMessage 转换 | `context-engine.ts` | L222-303 (`convertToAgentMessages`) |
| assistant content 规范化 | `context-engine.ts` | L305-315 (`normalizeAssistantContent`) |
| toolUse/toolResult 配对修复 | `session-transcript-repair.ts` | L342-347 (`sanitizeToolUseResultPairing`) |
| System Prompt 构建 | `context-engine.ts` | L353-392 (`buildSystemPromptAddition`) |
| Session ID 转换 | `context-engine.ts` | L177-197 (`openClawSessionToOvStorageId`) |
| OV API 调用 | `client.ts` | L482-492 (`getSessionContext`) |
| API 返回类型 | `client.ts` | L87-100 (`SessionContextResult`) |
| OV 消息类型 | `client.ts` | L61-80 (`OVMessagePart`, `OVMessage`) |
