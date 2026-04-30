# OpenClaw OpenViking 插件安装链路 Review（保留待修）

本文档梳理两条插件安装链路在当前提交（`ec2d9ab2 feat(openclaw-plugin): agent-driven install, setup hardening, schema-aware compat`）之上仍存在的可改进点，**不立刻修改，仅作为后续工作清单**。

涉及的两条安装链路：

- 路径 A：开发者路径 `npm i -g openclaw-openviking-setup-helper && ov-install`，入口 `examples/openclaw-plugin/setup-helper/install.js`。
- 路径 B：用户推荐路径 `openclaw plugins install @openclaw/openviking`，配置由 `examples/openclaw-plugin/commands/setup.ts` 提供（`openclaw openviking setup --json`）。

> 已在本轮提交修复：root API key 探测漏 400/422、setup --json 失败时安装器仍宣称完成、兼容性范围硬编码。详见 `commands/setup.ts`、`setup-helper/install.js`、`install-manifest.json` 的对应改动。

---

## P1 — 高优先级（建议尽快处理）

### #1 API key 通过命令行参数传给子进程（安全）

`setup-helper/install.js`：

```text
1740:1741:examples/openclaw-plugin/setup-helper/install.js
    if (effectiveRuntimeConfig.apiKey) {
      setupArgs.push("--api-key", effectiveRuntimeConfig.apiKey);
    }
```

之后通过 `runCapture("openclaw", setupArgs, ...)` 起子进程。

- Linux：同主机任意用户在 setup 进程存活期间可读 `/proc/<pid>/cmdline` 拿到完整 API key。
- Windows：任务管理器、`wmic process get CommandLine`、Process Explorer 都能看到。

**修法（建议）**

- `setup.ts` 新增对 `OPENVIKING_API_KEY` env 的读取（顶层早已读 `OPENVIKING_BASE_URL`，对称扩一行即可）。
- `install.js` 不再拼 `--api-key`，改为把 key 注入子进程 env：`runCapture("openclaw", setupArgs, { env: { ...ocEnv, OPENVIKING_API_KEY: apiKey }, shell: IS_WIN })`。
- 备选：增加 `--api-key-stdin` 子命令开关，从 stdin 读一行。

---

### #2 直写回退路径完全没做健康检查 / root-key 判定

```text
1820:1865:examples/openclaw-plugin/setup-helper/install.js
  if (!parsed) {
    // Direct write: only used when the installed plugin doesn't support `setup --json` (old version).
    ...
    await writeConfigDirect(pluginConfig, claimSlot ? pluginId : null);
    info(...);
  }
```

直写完不调健康检查、不探测 root key。
- 用 ROOT key 装老插件时一定运行时崩溃，但 install 这一路仍然"成功"。
- 服务端不可达时直写也不会被告知，只有 runtime 才发现。

**修法（建议）**

直写前最少做两件事：

1. `tryFetch(${baseUrl}/health)` 或类似的健康检查，失败且没传 `--allow-offline` → 走"runtime 配置失败"路径（与本轮已实现的逻辑一致）。
2. 如果服务端可达，再做一次 `GET /api/v1/sessions?limit=1`，状态码命中 400/401/403/422 时按 `setup.ts` 的 `probeApiKeyType` 同样规则识别 root key，缺 account/user 时至少 `warn`。

最干净的做法是把 `setup.ts` 的 `checkServiceHealth` + `probeApiKeyType` 抽到独立模块，两侧共用。

---

### #3 Windows 下 `shell: true` 子进程拼接，args 含特殊字符可能命令注入（安全）

`runCapture(cmd, args, { shell: IS_WIN })` 在 Windows 上会把 args 拼成单字符串交 cmd.exe 解析。当 `--api-key` 的值或 `--base-url` 中含 `&`、`|`、`<`、`>`、`%`、`"` 时，cmd.exe 会就地解释这些字符。

涉及调用点：

```text
482:482:examples/openclaw-plugin/setup-helper/install.js
1208:1208:examples/openclaw-plugin/setup-helper/install.js
1584:1584:examples/openclaw-plugin/setup-helper/install.js
1764:1764:examples/openclaw-plugin/setup-helper/install.js
```

**修法（建议）**

- Windows 上不用 `shell: true`，改成 `where openclaw` 解析绝对路径 + `spawn(absolutePath, args, { shell: false })`。
- 与 #1 联动：把 api-key 移出命令行后，最敏感的字段就不再走 cmd 解析。

---

### #4 `finalizePluginDeployment` 部署不原子，失败时用户彻底没东西

```text
1428:1437:examples/openclaw-plugin/setup-helper/install.js
async function finalizePluginDeployment(stagingDir) {
  await rm(PLUGIN_DEST, { recursive: true, force: true });
  try {
    await rename(stagingDir, PLUGIN_DEST);
  } catch {
    await cp(stagingDir, PLUGIN_DEST, { recursive: true, force: true });
    await rm(stagingDir, { recursive: true, force: true });
  }
  return info(tr(`Plugin deployed: ${PLUGIN_DEST}`, `插件部署完成: ${PLUGIN_DEST}`));
}
```

- 先 `rm PLUGIN_DEST` 再 `rename`，rename 失败再退到 `cp`。
- 如果 rename 失败、`cp` 也失败（断电、权限、磁盘满），原插件已删、新的没起来 — 用户**两端都没**。
- upgrade 模式有 audit 备份兜底，fresh install 没有。

**修法（建议）**

```text
async function finalizePluginDeployment(stagingDir) {
  const swap = `${PLUGIN_DEST}.swap-${process.pid}`;
  let swapped = false;
  if (existsSync(PLUGIN_DEST)) {
    await rename(PLUGIN_DEST, swap);
    swapped = true;
  }
  try {
    await rename(stagingDir, PLUGIN_DEST);
  } catch {
    try {
      await cp(stagingDir, PLUGIN_DEST, { recursive: true, force: true });
      await rm(stagingDir, { recursive: true, force: true });
    } catch (e) {
      if (swapped) {
        await rm(PLUGIN_DEST, { recursive: true, force: true }).catch(() => {});
        await rename(swap, PLUGIN_DEST).catch(() => {});
      }
      throw e;
    }
  }
  if (swapped) {
    await rm(swap, { recursive: true, force: true }).catch(() => {});
  }
  return info(tr(`Plugin deployed: ${PLUGIN_DEST}`, `插件部署完成: ${PLUGIN_DEST}`));
}
```

任何中间步骤失败都能回滚到原来的目录，不出现"两边都没"。

---

## P2 — 中优先级

### #5 `downloadPluginFile` 没设 AbortController 超时

```text
1321:1346:examples/openclaw-plugin/setup-helper/install.js
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(url);
```

同文件 `tryFetch`/`testRemoteFile` 都用了 10s timeout，独这里裸调 `fetch(url)`。GitHub raw 卡死或 TCP RST 后会让 ov-install 永久阻塞。

**修法**：每次 attempt 内部建独立 `AbortController`，60s（或可配）超时；abort 视为可重试错误。

---

### #6 GitHub API 不带 token，企业 NAT 后极易限流

```text
618:618:examples/openclaw-plugin/setup-helper/install.js
  const apiUrl = `https://api.github.com/repos/${REPO}/tags?per_page=100`;
```

未认证 IP 60 req/h；企业 NAT 共享出口后单 IP 通常立刻爆。

**修法**：读 `GH_TOKEN` / `GITHUB_TOKEN` env，若存在则在请求头加 `Authorization: Bearer <token>`。

---

### #7 `tryFetch` 把网络错误和"远程不存在"混淆

```text
548:559:examples/openclaw-plugin/setup-helper/install.js
async function tryFetch(url, timeout = 15000) {
  try {
    ...
    if (response.ok) {
      return await response.text();
    }
  } catch {}
  return null;
}
```

返回 `null` 同时表达 404 和网络错误。`resolvePluginConfig` 误判时会把"网络断了"当成"manifest 不存在"，落到 fallback 走更老的逻辑、报更难懂的错。

**修法**：返回 `{ ok, status, body, error }` 让调用方按需决策；保留旧 helper 兼容现有调用，新调用迁移到新返回结构。

---

### #8 二次补头探测复用同一 AbortController（本轮新引入）

`commands/setup.ts` `probeApiKeyType` 内部两次 `fetch` 共享同一 10s 超时。第一次用了 8s 时，第二次只剩 2s 才 abort。

**修法**：二次探测单独 `AbortController` + 独立 5s 超时，与首探测互不影响。

---

### #9 `setupJsonSupported` 用 grep 字符串判断

```text
1727:1730:examples/openclaw-plugin/setup-helper/install.js
    if (existsSync(setupTsPath)) {
      const setupSrc = await readFile(setupTsPath, "utf8");
      setupJsonSupported = setupSrc.includes('"--json"') || setupSrc.includes("'--json'");
    }
```

未来 setup.ts 注释/字符串里出现 `--json` 会假阳。

**修法**：

- 优先：读 `openclaw.plugin.json` 的 `commands` / `capabilities` 列表显式声明。
- 次选：尝试 `openclaw openviking setup --help` 解析输出，确认含 `--json` 短选项行。

---

## P3 — 设计 / UX 议题（需先讨论方向）

### #10 路径 B 下 setup 是手动两步，缺配置时静默加载

`INSTALL-AGENT.md` 描述：

```text
openclaw plugins install @openclaw/openviking
openclaw openviking setup --base-url <URL> --api-key <KEY> --json
```

如果用户 / agent 漏掉第二步直接 `openclaw gateway restart`，插件会静默加载但无配置，问题只在 logs 里能看到。

**讨论方向**

- 在 `index.ts` 启动时 detect `config.baseUrl` 为空 → `console.warn` 醒目一行 "OpenViking plugin loaded but not configured. Run: openclaw openviking setup --base-url ..."。
- 或在 plugin manifest 中声明 "post-install hook"（如果 OpenClaw 支持），由 OpenClaw 自动跑 setup。

---

### #11 install-manifest 字段读取分散在两个脚本

- `install.js` 读 `engines.openclaw`（package.json）+ `compatibility.minOpenvikingVersion`（manifest）。
- `setup.ts` 现在也读 `compatibility.minOpenvikingVersion`（本轮新增）。

不影响功能，但 source-of-truth 跨两个脚本。**修法**：抽 `setup-helper/utils/manifest.js`（或 `examples/openclaw-plugin/lib/manifest.ts`）共享读取逻辑。

---

### #12 prerelease tag 是否参与 latest 选取

```text
isSemverLike: /^v?\d+(\.\d+){1,2}$/
```

`v0.3.0-rc1` 不符合，被 `pickLatestPluginTag` 排除。

**讨论方向**：

- 若发布流程包含 RC 阶段且希望 `ov-install`（无显式版本时）优先选稳定版，**保持现状**。
- 若希望 `--prerelease` 选项能拿 RC，扩 regex + 增加 opt-in 开关。

---

### #13 `.gitignore` 下载失败自动写默认 `node_modules/`

```text
1349:1353:examples/openclaw-plugin/setup-helper/install.js
    if (fileName === ".gitignore") {
      await mkdir(dirname(destPath), { recursive: true });
      await writeFile(destPath, "node_modules/\n", "utf8");
      console.log(" OK");
      return;
    }
```

仅 404 时触发，不会覆盖远端有的版本。一般无害，但行为不直观。

**修法**：改成打印 "skip" 警告且不写文件；或保留写但在日志里说明 "remote .gitignore missing, used built-in default"。

---

## 改进优先级建议

- **P1** 应当尽快做，#1 / #3 是同主机用户能直接拿到 API key 的安全风险，#4 是首次安装失败时数据丢失。
- **P2** 可串成下一批，主要提升健壮性与可观测性。
- **P3** 需要先与产品 / Agent 接入方对齐再动手。

