# Vikingbot Console (Vue 3 + Vite)

基于 Vue 3 + Vite 重构的现代化前端界面。

## 开发

```bash
cd vikingbot/console/frontend
npm install
npm run dev
```

## 构建

```bash
npm run build
```

构建产物在 `dist` 目录，可以被 Python 后端静态文件服务提供。

## 核心特性

### 1. 基于 Schema 的动态表单

最核心的功能是 `SchemaForm` 和 `FormField` 组件，它们可以：
- 自动从 `/api/v1/config/schema` 加载配置 schema
- 根据 schema 类型动态渲染表单控件
- 支持嵌套对象、数组、布尔值、数字、字符串等
- 未来 schema 添加字段时，前端无需修改代码

### 2. 组件化架构

- `App.vue` - 主应用组件，路由管理
- `Dashboard.vue` - 仪表板页面
- `Config.vue` - 配置页面
- `SchemaForm.vue` - 动态表单容器
- `FormField.vue` - 单个字段渲染器
- `Sessions.vue` - 会话管理
- `Workspace.vue` - 工作区管理

## 与 Python 后端集成

1. Vite 开发模式配置了代理，`/api` 请求会被代理到 `http://localhost:8350`
2. 构建后，将 `dist` 目录复制到 `static` 目录，替换原有的 `index.html`

## 优势对比

| 特性 | 原生 HTML/jQuery | Vue 3 |
|------|-------------------|-------|
| 代码量 | 1400+ 行 | ~500 行 |
| 可维护性 | 低，逻辑分散 | 高，组件化 |
| 动态表单 | 需要手动写每个字段 | 自动基于 schema |
| 学习曲线 | 无 | 低 |
| 性能 | 好 | 更好 |
