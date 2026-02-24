# Vikingbot Console - Gradio 版本

使用 Gradio 实现的纯 Python 控制台界面。

## 安装依赖

```bash
pip install gradio
```

## 运行

```bash
python -m vikingbot.console.console_gradio
```

然后访问: http://localhost:8351

## 功能

### 1. Dashboard
- 显示系统状态
- 版本信息
- 会话统计

### 2. Config
- **Skills**: 复选框选择启用的技能
  - github-proxy
  - github
  - memory
  - cron
  - weather
  - tmux
  - skill-creator
  - summarize

- **Hooks**: 文本框配置 hook 路径（每行一个）
  - 示例: `vikingbot.hooks.builtins.openviking_hooks.hooks`

- **Full Configuration**: JSON 编辑器，完整配置

### 3. Sessions
- （待实现）

### 4. Workspace
- 工作区选择
- 文件浏览

## 与现有服务器集成

### 选项 1: 独立运行（推荐）
```bash
# 终端 1: 运行主服务
python -m vikingbot.console.server

# 终端 2: 运行 Gradio 控制台
python -m vikingbot.console.console_gradio
```

### 选项 2: 集成到 server.py
修改 `vikingbot/console/server.py`，添加 Gradio 挂载。

## 优势

| 特性 | 说明 |
|------|------|
| 纯 Python | 无需前端框架知识 |
| 代码量少 | ~250 行 vs 原 1400+ 行 |
| 组件丰富 | Gradio 提供大量现成组件 |
| 易维护 | 逻辑清晰，修改简单 |
