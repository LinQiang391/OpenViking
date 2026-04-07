# OpenClaw Gateway Profile 清理脚本

## 脚本位置

```
examples/openclaw-plugin/tests/cleanup_gateway.py
```

## 前置要求

- **Python 3.8+**（零外部依赖，只用标准库）

---

## 背景

使用 `deploy_gateway.py` 部署的每个 profile 会在用户 home 目录下创建独立的状态目录：

```
~/.openclaw/            ← profile: default
~/.openclaw-second/     ← profile: second
~/.openclaw-eval-ov/    ← profile: eval-ov
~/.openclaw-<name>/     ← profile: <name>
```

这些目录包含配置文件、会话数据、工作区（含 git 仓库）、日志等，测试完成后可能占用较大磁盘空间。本脚本用于安全清理不再需要的 profile。

---

## 用法

### 列出所有已有 profile

```powershell
python examples/openclaw-plugin/tests/cleanup_gateway.py --list
```

输出示例：

```
Found 5 profile(s):

  Profile               Directory                                             Size      Note
  --------------------  --------------------------------------------------  --------  ------------
  default               C:\Users\xxx\.openclaw                                12.3 MB  (has config)
  eval-mc               C:\Users\xxx\.openclaw-eval-mc                      2125.8 MB  (has config)
  eval-ov               C:\Users\xxx\.openclaw-eval-ov                      5821.8 MB  (has config)
  second                C:\Users\xxx\.openclaw-second                         45.2 MB  (has config)
  third                 C:\Users\xxx\.openclaw-third                           8.7 MB  (no config)
```

### 预览删除（dry-run，默认）

不加 `--force` 时只显示会删除什么，不实际执行：

```powershell
python examples/openclaw-plugin/tests/cleanup_gateway.py eval-ov eval-mc
```

输出：

```
[DRY-RUN] Cleanup targets:

  [WOULD DELETE] eval-ov               C:\Users\xxx\.openclaw-eval-ov  (5821.8 MB)
  [WOULD DELETE] eval-mc               C:\Users\xxx\.openclaw-eval-mc  (2125.8 MB)

This was a dry-run. Add --force to actually delete.
```

### 确认删除

```powershell
python examples/openclaw-plugin/tests/cleanup_gateway.py eval-ov eval-mc --force
```

输出：

```
Cleanup targets:

  [DELETED] eval-ov               C:\Users\xxx\.openclaw-eval-ov  (5821.8 MB)
  [DELETED] eval-mc               C:\Users\xxx\.openclaw-eval-mc  (2125.8 MB)
```

### 通配符批量匹配

使用 `--pattern` 按通配符匹配 profile 名称：

```powershell
# 预览所有 eval- 开头的 profile
python examples/openclaw-plugin/tests/cleanup_gateway.py --pattern "eval-*"

# 确认删除
python examples/openclaw-plugin/tests/cleanup_gateway.py --pattern "eval-*" --force
```

### 混合使用

可以同时指定具体名称和通配符：

```powershell
python examples/openclaw-plugin/tests/cleanup_gateway.py third --pattern "eval-*" --force
```

---

## 参数说明

| 参数 | 说明 |
|------|------|
| `profiles` | 要删除的 profile 名称，可以指定多个（位置参数） |
| `--list` | 列出所有已有的 gateway profile 及其占用空间 |
| `--pattern` | 用通配符匹配 profile 名称（如 `eval-*`、`test-??`） |
| `--force` | 实际执行删除。不加此参数只预览（dry-run） |

---

## 安全机制

- **默认 dry-run**：不加 `--force` 绝不会删除任何文件，只显示将要执行的操作
- **逐项报告**：每个 profile 单独显示删除结果或错误信息
- **不存在则跳过**：指定的 profile 不存在时标记 `[SKIP]`，不会报错

---

## 与 deploy_gateway.py 配合

典型的测试-清理工作流：

```powershell
# 1. 部署测试环境
python examples/openclaw-plugin/tests/deploy_gateway.py --profile eval-ov --port 19100 --ov-api-key <key>

# 2. 运行测试 ...

# 3. 测试完成，清理
python examples/openclaw-plugin/tests/cleanup_gateway.py eval-ov --force
```
