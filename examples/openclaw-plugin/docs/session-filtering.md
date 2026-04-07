# OpenViking 插件中的 Session 过滤

这份文档说明 `examples/openclaw-plugin` 如何对齐 `lossless-claw/docs/session-filtering.md` 里的
`ignoreSessionPatterns` 设计，并把它映射到 OpenViking 插件当前的自动化链路里。

## 目标

我们希望给 OpenViking 插件补上一层“按 session 全局忽略”的能力：

- 命中的 session 不再参与插件自动上下文组装
- 命中的 session 不再触发插件自动写入/提交
- 命中的 session 不再触发 prompt 前自动 recall 或 ingest reply assist

这项能力的定位，和 lossless-claw 里的 `ignoreSessionPatterns` 一致：

`sessionKey/sessionId -> ignore decision -> 是否参与自动链路`

## 为什么需要一份 OpenViking 版本说明

lossless-claw 的原始文档围绕的是：

- `sessionKey/sessionId -> conversationId`
- 读写都围绕 LCM `conversationId` 做 scope 解析

而 OpenViking 插件当前并没有一套独立的 `conversationId` store。它更接近：

- 用 `sessionKey/sessionId` 派生 OpenViking `ovSessionId`
- `assemble()` 从 OpenViking session context 回读
- `afterTurn()` / `compact()` / `before_reset` 向 OpenViking session 写入或提交
- `before_prompt_build` 做 auto-recall 和 ingest reply assist

所以这里的对齐重点不是 conversation scope，而是：

“一个 session 是否参与 OpenViking 插件的自动生命周期”

## 配置项

新增配置：

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "config": {
          "ignoreSessionPatterns": [
            "agent:*:cron:**",
            "agent:main:subagent:**"
          ]
        }
      }
    }
  }
}
```

默认值：

```json
[]
```

## Pattern 语法

沿用 lossless-claw 的 glob 语义：

- `*` 匹配任意非 `:` 字符
- `**` 匹配任意字符，包括 `:`
- 默认匹配完整 `sessionKey`

例子：

- `agent:*:cron:**`
  匹配 `agent:main:cron:nightly:run:1`
- `agent:main:subagent:**`
  匹配 `agent:main:subagent:abc`

## 匹配规则

匹配顺序与 lossless-claw 保持一致：

1. 优先使用 `sessionKey`
2. 如果没有 `sessionKey`，回退到 `sessionId`

也就是说，忽略判定使用的是：

`sessionKey ?? sessionId`

这保证了：

- 逻辑会话稳定时，优先按更稳定的 `sessionKey` 控制
- 旧调用点没有 `sessionKey` 时，也仍然可以通过 `sessionId` 做兜底过滤

## 自动链路中的生效点

### 1. `before_prompt_build`

命中 `ignoreSessionPatterns` 后：

- 不做 auto-recall
- 不注入 `<relevant-memories>`
- 不做 ingest reply assist
- 不注入 `<ingest-reply-assist>`

这对应 lossless-claw 里的“不要从 LCM assemble 上下文”。

### 2. `assemble()`

命中后：

- 不访问 OpenViking session context
- 直接回退到 OpenClaw 提供的 live messages

也就是说，忽略 session 不参与自动历史装配。

### 3. `afterTurn()`

命中后：

- 不调用 `addSessionMessage`
- 不读取 `pending_tokens`
- 不触发自动 `commit(wait=false)`

这对应 lossless-claw 里的“跳过 after-turn 持久化”。

### 4. `compact()`

命中后：

- 不调用 OpenViking `commit(wait=true)`
- 返回 `ok=true`、`compacted=false`、`reason="ignore_session_pattern"`

这里把“被配置跳过”视为一条受控的 no-op，而不是错误。

### 5. `before_reset`

命中后：

- 不执行 reset 前的 OpenViking commit

这对应 lossless-claw 里的生命周期写路径过滤。

## 不在本期范围内的部分

本期**没有**把忽略能力扩展到显式工具调用：

- `memory_recall`
- `memory_store`
- `memory_forget`
- `ov_archive_expand`

原因是这些工具属于“显式用户/模型意图驱动”的手动入口，而不是插件自动生命周期的一部分。

换句话说，本期语义是：

- 忽略自动链路
- 保留显式工具作为手动兜底入口

如果未来需要“命中 ignore 后连显式工具也禁用”，可以在工具工厂层再补一层同样的判定。

## 实现文件

这次实现主要落在以下文件：

- `examples/openclaw-plugin/config.ts`
- `examples/openclaw-plugin/text-utils.ts`
- `examples/openclaw-plugin/index.ts`
- `examples/openclaw-plugin/context-engine.ts`
- `examples/openclaw-plugin/openclaw.plugin.json`

测试覆盖：

- `examples/openclaw-plugin/__tests__/ingest-reply-assist-session-patterns.test.ts`
- `examples/openclaw-plugin/tests/ut/config.test.ts`
- `examples/openclaw-plugin/tests/ut/context-engine-assemble.test.ts`
- `examples/openclaw-plugin/tests/ut/context-engine-afterTurn.test.ts`
- `examples/openclaw-plugin/tests/ut/context-engine-compact.test.ts`
- `examples/openclaw-plugin/tests/ut/plugin-ignore-session-patterns.test.ts`

## 心智模型

OpenViking 插件里最稳妥的理解方式是：

1. 先用 `ignoreSessionPatterns` 判断当前 session 是否应该被插件完全忽略
2. 如果被忽略，就不参与任何自动的 recall / assemble / capture / compact / reset-commit
3. 如果未被忽略，才继续按原有链路用 `sessionKey/sessionId` 推导 `ovSessionId`
4. 后续读写都围绕这个 `ovSessionId` 继续执行

主线可以概括为：

`sessionKey/sessionId -> ignore decision -> ovSessionId -> OpenViking 自动链路`
