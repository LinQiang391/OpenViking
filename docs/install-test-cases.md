# ov-install 安装脚本测试用例

> 版本：0.3.0-beta.3  
> 测试命令：`npx -p openclaw-openviking-setup-helper@beta ov-install [OPTIONS]`

---

## 一、环境准备

### 前置条件

| 依赖 | 最低版本 | 验证命令 |
|---|---|---|
| Node.js | 22.0.0 | `node --version` |
| Python | 3.10 | `python3 --version` |
| OpenClaw | 已安装 | `openclaw --version` |
| pip | 最新 | `python3 -m pip --version` |

### 测试环境变量

```bash
# 可选：跳过 OpenViking 安装（仅测试插件部署）
export SKIP_OPENVIKING=1

# 可选：跳过 OpenClaw 检查
export SKIP_OPENCLAW=1

# 可选：提供 API Key（测试 -y 模式）
export OPENVIKING_VLM_API_KEY=test-key
export OPENVIKING_EMBEDDING_API_KEY=test-key
```

### 清理脚本

```bash
# 清理已安装的插件（恢复到首次安装状态）
rm -rf ~/.openclaw/extensions/openviking
rm -rf ~/.openclaw/extensions/memory-openviking
rm -rf ~/.openclaw/.openviking-install-state
rm -rf ~/.openviking
# 注意：不要删除 ~/.openclaw/openclaw.json 中的其他插件配置
```

---

## 二、首次安装

### TC-01: 首次安装 — 交互模式 + Local

**命令：**
```bash
ov-install
```

**前置：** 无已安装插件

**预期流程：**
1. 显示安装目标信息（仓库、版本）
2. 未检测到已安装插件，进入模式选择
3. 选择 "Local" 模式
4. 检测 Python 环境
5. 检测 OpenClaw
6. 安装 OpenViking Python 服务（pip install）
7. 收集 ov.conf 配置（VLM API Key、Embedding API Key、高级选项）
8. 下载插件文件，显示每个文件的状态（✓/–/✗）
9. 安装 npm 依赖
10. 原子写入 openclaw.json
11. 生成环境变量文件
12. 显示启动命令

**验证点：**
- [ ] `~/.openclaw/extensions/openviking/` 目录存在且包含插件文件
- [ ] `~/.openclaw/openclaw.json` 中包含 `openviking` 的 entries、allow、load.paths、slots.contextEngine
- [ ] `~/.openviking/ov.conf` 存在且包含用户输入的配置
- [ ] `~/.openclaw/.openviking-install-state/openviking.json` 存在
- [ ] `openviking.env` 或 `openviking.env.ps1` 存在

---

### TC-02: 首次安装 — 交互模式 + Remote

**命令：**
```bash
ov-install
```

**前置：** 无已安装插件

**预期流程：**
1. 选择 "Remote" 模式
2. 输入远程服务器 URL、API Key、Agent ID
3. 下载插件文件
4. 写入 openclaw.json（mode: remote, baseUrl 等）
5. 不安装 OpenViking Python 服务
6. 不生成 ov.conf

**验证点：**
- [ ] openclaw.json 中 entries.openviking.config.mode === "remote"
- [ ] openclaw.json 中 entries.openviking.config.baseUrl 为用户输入的 URL
- [ ] 不存在 `~/.openviking/ov.conf`
- [ ] 不执行 pip install openviking

---

### TC-03: 首次安装 — 非交互模式 + Local（默认）

**命令：**
```bash
ov-install -y
```

**前置：** 无已安装插件

**预期流程：**
1. 自动选择 local 模式
2. 安装 OpenViking Python 服务
3. 生成默认 ov.conf
4. 下载并部署插件
5. 配置 openclaw.json
6. 显示"重要：启动 OpenViking 前必须先修改 ov.conf！"提示

**验证点：**
- [ ] 全程无交互提示
- [ ] ov.conf 使用默认值生成
- [ ] 安装完成后显示 ov.conf 手动修改提示（因为没有 VLM API Key）
- [ ] openclaw.json 配置正确

---

### TC-04: 首次安装 — 非交互模式 + Local + 环境变量提供 API Key

**命令：**
```bash
OPENVIKING_VLM_API_KEY=xxx ov-install -y
```

**前置：** 无已安装插件

**验证点：**
- [ ] ov.conf 中 vlm.api_key 为 "xxx"
- [ ] 安装完成后**不显示** ov.conf 手动修改提示

---

### TC-05: 首次安装 — 非交互模式 + Remote

**命令：**
```bash
ov-install -y --mode remote --remote-url http://10.0.0.1:1933
```

**前置：** 无已安装插件

**验证点：**
- [ ] 自动选择 remote 模式
- [ ] openclaw.json 中 mode === "remote"，baseUrl === "http://10.0.0.1:1933"
- [ ] 不安装 OpenViking Python 服务
- [ ] 不生成 ov.conf

---

### TC-06: 首次安装 — 非交互模式 + Remote 但未提供 URL

**命令：**
```bash
ov-install -y --mode remote
```

**预期：** 报错退出，提示 `--mode remote requires --remote-url in non-interactive mode (-y)`

---

### TC-07: 首次安装 — 指定版本

**命令：**
```bash
ov-install --plugin-version v0.3.3
```

**验证点：**
- [ ] 安装 v0.3.3 版本的插件
- [ ] OpenViking Python 版本同步为 0.3.3
- [ ] install-state 中 requestedRef === "v0.3.3"

---

### TC-08: 首次安装 — 使用 --version 简写

**命令：**
```bash
ov-install --version 0.3.3
```

**验证点：**
- [ ] 等同于 `--plugin-version v0.3.3 --openviking-version 0.3.3`

---

## 三、非首次安装（检测到已安装插件）

### TC-10: 非首次安装 — 交互模式 + Local 已安装

**命令：**
```bash
ov-install
```

**前置：** 已通过 TC-01 安装了 local 模式的插件

**预期流程：**
1. 检测到已安装插件，显示当前版本
2. 弹出选择菜单（4 个选项）：
   - 仅升级插件（保留 OpenViking 服务不变）
   - 升级插件 + OpenViking 服务（大版本更新推荐）
   - 全新安装（覆盖所有内容）
   - 取消
3. 选择升级后，显示可用版本列表（最多 10 个），默认选最新

**验证点：**
- [ ] 正确显示已安装版本
- [ ] 4 个选项都可选
- [ ] 版本列表从 GitHub 获取，按版本号降序排列
- [ ] 默认选中最新版本

---

### TC-11: 非首次安装 — 交互 + 选择"仅升级插件"

**前置：** TC-10 的基础上选择"仅升级插件"

**预期流程：**
1. 选择目标版本
2. 备份 openclaw.json 和旧插件目录
3. 停止 OpenClaw gateway
4. 版本比较（同版本/降级/升级）
5. 清理旧插件配置
6. 下载并部署新版插件
7. 回填运行时配置（保留原有 mode、port 等）
8. 生成升级审计文件

**验证点：**
- [ ] 旧插件目录被备份到 `.openviking.backup-xxx`
- [ ] openclaw.json 被备份
- [ ] 新插件文件部署成功
- [ ] ov.conf 未被修改
- [ ] OpenViking Python 服务未被重新安装
- [ ] 升级审计文件存在

---

### TC-12: 非首次安装 — 交互 + 选择"升级插件 + OpenViking 服务"

**前置：** 已安装 local 模式插件

**预期流程：**
1. 选择目标版本
2. 跳过模式选择（已知 local）
3. 检测 Python 环境
4. 执行 `pip install --upgrade openviking==<version>`
5. 保留 ov.conf（已存在时默认保留）
6. 下载并部署新版插件
7. 写入 openclaw.json

**验证点：**
- [ ] OpenViking Python 服务被升级
- [ ] ov.conf 保留不变
- [ ] 插件文件更新

---

### TC-13: 非首次安装 — 交互 + 选择"全新安装"

**前置：** 已安装插件

**预期：** 走完整的首次安装流程（选模式、收集配置等），覆盖所有内容

---

### TC-14: 非首次安装 — 交互 + 选择"取消"

**预期：** 显示"安装已取消"并退出，不做任何修改

---

### TC-15: 非首次安装 — 非交互 + Local 已安装

**命令：**
```bash
ov-install -y
```

**前置：** 已安装 local 模式插件

**预期流程：**
1. 自动检测到已安装插件
2. 显示"非交互模式：自动升级（检测到模式: local）"
3. 自动升级插件 + OpenViking 服务到最新版
4. 保留 ov.conf 不动

**验证点：**
- [ ] 全程无交互
- [ ] OpenViking 服务被升级
- [ ] 插件被升级
- [ ] ov.conf 未被修改

---

### TC-16: 非首次安装 — 非交互 + Remote 已安装

**命令：**
```bash
ov-install -y
```

**前置：** 已安装 remote 模式插件

**预期：**
- 自动检测到 remote 模式
- 仅升级插件（不安装 OpenViking 服务）
- 保留 remote 配置

---

### TC-17: 非首次安装 — 指定版本升级

**命令：**
```bash
ov-install -y --plugin-version v0.3.5
```

**前置：** 已安装 v0.3.3

**验证点：**
- [ ] 升级到指定的 v0.3.5 版本
- [ ] 不显示版本选择列表（因为已显式指定）

---

## 四、版本比较场景

### TC-20: 升级 — 目标版本更高

**命令：**
```bash
ov-install --upgrade --plugin-version v0.3.5
```

**前置：** 已安装 v0.3.3

**预期：** 正常升级，无额外提示

---

### TC-21: 升级 — 目标版本相同

**命令：**
```bash
ov-install --upgrade --plugin-version v0.3.3
```

**前置：** 已安装 v0.3.3

**预期：** 显示"插件已经是 v0.3.3 版本，无需升级。"并退出

---

### TC-22: 升级 — 目标版本更低（降级）— 交互

**命令：**
```bash
ov-install --upgrade --plugin-version v0.3.1
```

**前置：** 已安装 v0.3.3

**预期流程：**
1. 显示警告"目标版本 v0.3.1 低于已安装的 v0.3.3，这是一次降级操作。"
2. 提示确认"确认继续降级？"（默认 No）
3. 用户确认后继续降级

---

### TC-23: 升级 — 目标版本更低（降级）— 非交互

**命令：**
```bash
ov-install --upgrade --plugin-version v0.3.1 -y
```

**前置：** 已安装 v0.3.3

**预期：** 显示降级警告后直接继续（非交互不弹确认）

---

## 五、回滚

### TC-30: 回滚最近一次升级

**命令：**
```bash
ov-install --rollback
```

**前置：** 刚执行过 TC-11 的升级操作

**预期：**
- 从升级审计文件中读取备份路径
- 恢复 openclaw.json 备份
- 恢复旧插件目录
- 显示回滚成功

**验证点：**
- [ ] openclaw.json 恢复到升级前的状态
- [ ] 插件目录恢复到升级前的版本
- [ ] 审计文件标记 rolledBackAt

---

### TC-31: 回滚 — 无升级记录

**命令：**
```bash
ov-install --rollback
```

**前置：** 没有执行过升级

**预期：** 报错提示无可回滚的升级记录

---

### TC-32: --upgrade 和 --rollback 同时使用

**命令：**
```bash
ov-install --upgrade --rollback
```

**预期：** 报错 `--update/--upgrade-plugin and --rollback cannot be used together`

---

## 六、旧版插件迁移

### TC-40: 从旧版 memory-openviking 升级到新版 openviking

**前置：** 手动在 openclaw.json 中配置旧版 `memory-openviking` 插件：
```json
{
  "plugins": {
    "allow": ["memory-openviking"],
    "entries": {
      "memory-openviking": {
        "config": {
          "mode": "local",
          "configPath": "~/.openviking/ov.conf",
          "port": 1933,
          "targetUri": "viking://user/memories",
          "autoRecall": true,
          "autoCapture": true
        }
      }
    },
    "slots": { "memory": "memory-openviking" },
    "load": { "paths": ["~/.openclaw/extensions/memory-openviking"] }
  }
}
```

**命令：**
```bash
ov-install
```

**预期流程：**
1. 检测到 generation: "legacy"
2. 提示已安装旧版插件
3. 选择升级后：
   - 备份旧版配置和目录
   - 从 openclaw.json 中清除 `memory-openviking` 的所有配置
   - 部署新版 `openviking` 插件
   - 写入新版配置（继承 mode: local, port: 1933 等运行时参数）

**验证点：**
- [ ] openclaw.json 中不再包含 `memory-openviking`
- [ ] openclaw.json 中包含 `openviking` 的完整配置
- [ ] `plugins.slots.memory` 被重置为 "none"
- [ ] `plugins.slots.contextEngine` 被设为 "openviking"
- [ ] 运行时配置（mode、port）从旧版继承
- [ ] `extensions/memory-openviking` 被备份
- [ ] `extensions/openviking` 包含新版文件

---

## 七、多 OpenClaw 实例

### TC-50: 多实例 — 交互模式选择

**前置：** 存在 `~/.openclaw` 和 `~/.openclaw-dev` 两个实例

**命令：**
```bash
ov-install
```

**预期：** 弹出实例选择菜单，让用户选择安装到哪个实例

---

### TC-51: 多实例 — 通过 --workdir 指定

**命令：**
```bash
ov-install --workdir ~/.openclaw-dev
```

**预期：** 直接安装到 `~/.openclaw-dev`，不弹选择菜单

---

## 八、网络错误处理

### TC-60: 插件文件下载失败 — 网络超时

**模拟：** 断开网络或设置防火墙阻止 GitHub 访问

**命令：**
```bash
ov-install -y
```

**预期：**
- 每个文件重试 3 次（递增退避 2s → 4s → 6s）
- 失败文件显示 ✗ FAILED
- 最终显示：`下载失败，网络连接异常（无法访问 GitHub）。请检查网络后重新执行安装。`

---

### TC-61: 插件文件下载失败 — 部分文件 404

**模拟：** 使用一个不存在的版本标签

**命令：**
```bash
ov-install --plugin-version v99.99.99
```

**预期：** 必需文件 404 时报错退出，可选文件 404 时标记 skipped

---

### TC-62: 版本解析失败 — GitHub API 不可达

**模拟：** 断开网络

**命令：**
```bash
ov-install
```

**预期：**
- 先尝试 GitHub API，失败后回退到 `git ls-remote`
- 两者都失败时，提示用户使用 `--plugin-version` 显式指定

---

## 九、ov.conf 处理

### TC-70: ov.conf 不存在 — 交互模式

**前置：** 删除 `~/.openviking/ov.conf`

**预期：** 收集配置（VLM Key、Embedding Key、高级选项）后生成 ov.conf

---

### TC-71: ov.conf 已存在 — 交互模式

**前置：** ov.conf 已存在

**预期：**
- 提示"ov.conf 已存在，是否重新配置？"（默认 No）
- 选择 No 时保留现有配置
- 选择 Yes 时重新收集配置

---

### TC-72: ov.conf 已存在 — 非交互模式

**前置：** ov.conf 已存在

**命令：**
```bash
ov-install -y
```

**预期：** 自动保留现有 ov.conf，显示"已保留现有配置"

---

### TC-73: ov.conf 不存在 — 非交互模式 — 无 API Key

**前置：** 删除 ov.conf，不设置环境变量

**命令：**
```bash
ov-install -y
```

**预期：**
- 生成默认 ov.conf（api_key 为 null）
- 安装完成后显示醒目提示：`重要：启动 OpenViking 前必须先修改 ov.conf！`

---

### TC-74: ov.conf 不存在 — 非交互模式 — 有 API Key

**命令：**
```bash
OPENVIKING_VLM_API_KEY=xxx ov-install -y
```

**预期：**
- 生成 ov.conf，vlm.api_key 为 "xxx"
- 安装完成后**不显示**手动修改提示

---

## 十、openclaw.json 原子写入

### TC-80: 验证 openclaw.json 只被写入一次

**方法：** 使用 `inotifywait` 或 `fswatch` 监控 `openclaw.json` 的写入次数

**命令：**
```bash
# 终端 1：监控文件
fswatch ~/.openclaw/openclaw.json

# 终端 2：执行安装
ov-install -y
```

**验证点：**
- [ ] openclaw.json 在整个安装过程中只被写入 1 次（原子写入）
- [ ] 不会触发多次 OpenClaw gateway 重启

---

### TC-81: 验证不影响其他插件配置

**前置：** openclaw.json 中已有其他插件配置：
```json
{
  "plugins": {
    "allow": ["other-plugin"],
    "entries": { "other-plugin": { "config": { "key": "value" } } },
    "load": { "paths": ["/path/to/other-plugin"] }
  }
}
```

**命令：**
```bash
ov-install -y
```

**验证点：**
- [ ] `other-plugin` 的 entries、allow、load.paths 完全不变
- [ ] 只新增了 `openviking` 相关的配置

---

## 十一、Python 虚拟环境

### TC-90: Linux — externally-managed-environment 错误

**前置：** Linux 系统，Python 受 PEP 668 保护

**命令：**
```bash
ov-install -y
```

**预期：**
- pip install 失败后自动创建虚拟环境 `~/.openviking/venv/`
- 在虚拟环境中安装 OpenViking
- 环境变量文件中 OPENVIKING_PYTHON 指向 venv 中的 python

---

### TC-91: 已有虚拟环境 — 复用

**前置：** `~/.openviking/venv/` 已存在且包含 openviking

**预期：** 直接在已有虚拟环境中执行 `pip install --upgrade`，不重新创建

---

## 十二、CLI 参数验证

### TC-100: --help

**命令：**
```bash
ov-install --help
```

**验证点：**
- [ ] 显示所有可用参数
- [ ] 包含 --mode、--remote-url、--remote-api-key、--remote-agent-id

---

### TC-101: --current-version

**命令：**
```bash
ov-install --current-version
```

**预期：** 显示已安装的插件版本和 OpenViking Python 版本，然后退出

---

### TC-102: --version 和 --plugin-version 同时使用

**命令：**
```bash
ov-install --version 0.3.3 --plugin-version v0.3.3
```

**预期：** 报错 `--version cannot be used together with --plugin-version or --openviking-version`

---

### TC-103: --mode 无效值

**命令：**
```bash
ov-install --mode invalid
```

**预期：** 报错 `--mode must be "local" or "remote", got "invalid"`

---

### TC-104: --zh 中文模式

**命令：**
```bash
ov-install --zh
```

**验证点：** 所有提示信息显示为中文

---

### TC-105: --mode local 显式指定

**命令：**
```bash
ov-install --mode local
```

**预期：** 跳过模式选择，直接进入 local 安装流程

---

## 十三、PyPI 镜像回退

### TC-110: 国内镜像失败 — 自动回退到官方 PyPI

**模拟：** 默认镜像 `mirrors.volces.com` 不可达

**预期：**
- 显示"Install from mirror failed. Retrying with official PyPI"
- 自动回退到 `https://pypi.org/simple/`

---

### TC-111: 用户指定 PIP_INDEX_URL — 不回退

**命令：**
```bash
PIP_INDEX_URL=https://custom-mirror.com/simple/ ov-install -y
```

**预期：** 使用用户指定的镜像，失败时不回退到官方 PyPI

---

## 十四、本地仓库开发模式

### TC-120: 使用本地仓库安装

**命令：**
```bash
ov-install --repo /path/to/OpenViking
```

**预期：**
- OpenViking 通过 `pip install -e /path/to/OpenViking` 安装（开发模式）
- 插件文件从本地仓库复制（不从 GitHub 下载）

---

## 十五、跨平台

### TC-130: Windows — PowerShell

**验证点：**
- [ ] 路径使用 `\` 分隔符
- [ ] 环境变量文件生成 `.ps1` 和 `.bat` 两种格式
- [ ] Python 命令使用 `py` 作为候选

---

### TC-131: macOS — zsh

**验证点：**
- [ ] 路径使用 `/` 分隔符
- [ ] 环境变量文件为 shell 格式
- [ ] Python 命令使用 `python3`

---

### TC-132: Linux — bash

**验证点：**
- [ ] 同 macOS
- [ ] 虚拟环境处理（PEP 668）

---

## 十六、边界场景

### TC-140: 安装过程中 Ctrl+C 取消

**预期：** 显示"Installation cancelled."并退出，不留下半成品文件（staging 目录被清理）

---

### TC-141: 磁盘空间不足

**预期：** 文件写入失败时给出有意义的错误信息

---

### TC-142: openclaw.json 格式损坏

**前置：** 手动破坏 openclaw.json 为无效 JSON

**预期：** 安装时能处理（重新创建或报错提示）

---

### TC-143: 并发安装

**模拟：** 同时在两个终端执行 `ov-install`

**预期：** staging 目录使用 PID + 时间戳命名，避免冲突

---

### TC-144: 插件版本 v0.2.7（已知不存在的版本）

**命令：**
```bash
ov-install --plugin-version v0.2.7
```

**预期：** 报错 `Plugin version v0.2.7 does not exist.`
