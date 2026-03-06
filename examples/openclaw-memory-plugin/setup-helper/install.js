#!/usr/bin/env node
/**
 * OpenClaw + OpenViking cross-platform installer
 *
 * One-liner (after npm publish; use package name + bin name):
 *   npx -p openclaw-openviking-setup-helper ov-install [ -y ] [ --zh ]
 * Or install globally then run:
 *   npm i -g openclaw-openviking-setup-helper
 *   ov-install
 *   openclaw-openviking-install
 *
 * Direct run: node install.js [ -y | --yes ] [ --zh ] [ --openviking-version=V ] [ --repo=PATH ]
 *
 * Environment variables (see install.sh / install.ps1):
 *   REPO, BRANCH, OPENVIKING_INSTALL_YES, SKIP_OPENCLAW, SKIP_OPENVIKING
 *   OPENVIKING_VERSION       Pip install openviking==VERSION (omit for latest)
 *   OPENVIKING_REPO          Repo path: source install (pip -e) + local plugin (default: off)
 *   NPM_REGISTRY, PIP_INDEX_URL
 *   OPENVIKING_VLM_API_KEY, OPENVIKING_EMBEDDING_API_KEY, OPENVIKING_ARK_API_KEY
 *   OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES (Linux), GET_PIP_URL
 */

import { spawn } from "node:child_process";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));

const REPO = process.env.REPO || "volcengine/OpenViking";
const BRANCH = process.env.BRANCH || "main";
const GH_RAW = `https://raw.githubusercontent.com/${REPO}/${BRANCH}`;
const NPM_REGISTRY = process.env.NPM_REGISTRY || "https://registry.npmmirror.com";
const PIP_INDEX_URL = process.env.PIP_INDEX_URL || "https://pypi.tuna.tsinghua.edu.cn/simple";

const IS_WIN = process.platform === "win32";
const HOME = process.env.HOME || process.env.USERPROFILE || "";
const OPENCLAW_DIR = join(HOME, ".openclaw");
const OPENVIKING_DIR = join(HOME, ".openviking");
const PLUGIN_DEST = join(OPENCLAW_DIR, "extensions", "memory-openviking");

const DEFAULT_SERVER_PORT = 1933;
const DEFAULT_AGFS_PORT = 1833;
const DEFAULT_VLM_MODEL = "doubao-seed-2-0-pro-260215";
const DEFAULT_EMBED_MODEL = "doubao-embedding-vision-250615";

const PLUGIN_FILES = [
  "examples/openclaw-memory-plugin/index.ts",
  "examples/openclaw-memory-plugin/config.ts",
  "examples/openclaw-memory-plugin/openclaw.plugin.json",
  "examples/openclaw-memory-plugin/package.json",
  "examples/openclaw-memory-plugin/package-lock.json",
  "examples/openclaw-memory-plugin/.gitignore",
];

let installYes = process.env.OPENVIKING_INSTALL_YES === "1";
let langZh = false;
let openvikingVersion = process.env.OPENVIKING_VERSION || "";
let openvikingRepo = process.env.OPENVIKING_REPO || "";
for (const a of process.argv.slice(2)) {
  if (a === "-y" || a === "--yes") installYes = true;
  if (a === "--zh") langZh = true;
  if (a === "-h" || a === "--help") {
    console.log("Usage: node install.js [ -y | --yes ] [ --zh ] [ --openviking-version=V ] [ --repo=PATH ]");
    console.log("");
    console.log("  -y, --yes   Non-interactive (use defaults)");
    console.log("  --zh       Chinese prompts");
    console.log("  --openviking-version=VERSION   Pip install openviking==VERSION (default: latest)");
    console.log("  --repo=PATH   Use OpenViking repo at PATH: pip install -e PATH, plugin from repo (default: off)");
    console.log("  -h, --help  This help");
    console.log("");
    console.log("Env: OPENVIKING_REPO (repo path for source install), REPO, BRANCH, SKIP_OPENCLAW, SKIP_OPENVIKING, OPENVIKING_VERSION, NPM_REGISTRY, PIP_INDEX_URL");
    process.exit(0);
  }
  if (a.startsWith("--openviking-version=")) {
    openvikingVersion = a.slice("--openviking-version=".length).trim();
  }
  if (a.startsWith("--repo=")) {
    openvikingRepo = a.slice("--repo=".length).trim();
  }
}
const OPENVIKING_PIP_SPEC = openvikingVersion ? `openviking==${openvikingVersion}` : "openviking";

function tr(en, zh) {
  return langZh ? zh : en;
}

function info(msg) {
  console.log(`[INFO] ${msg}`);
}
function warn(msg) {
  console.log(`[WARN] ${msg}`);
}
function err(msg) {
  console.log(`[ERROR] ${msg}`);
}
function bold(msg) {
  console.log(msg);
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, {
      stdio: opts.silent ? "pipe" : "inherit",
      shell: opts.shell ?? true,
      ...opts,
    });
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`exit ${code}`))));
  });
}

function runCapture(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: opts.shell ?? false,
      ...opts,
    });
    let out = "";
    let errOut = "";
    p.stdout?.on("data", (d) => (out += d));
    p.stderr?.on("data", (d) => (errOut += d));
    p.on("error", (e) => resolve({ code: -1, out: "", err: String(e) }));
    p.on("close", (code) => resolve({ code, out: out.trim(), err: errOut.trim() }));
  });
}

function question(prompt, defaultValue = "") {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const def = defaultValue ? ` [${defaultValue}]` : "";
  return new Promise((resolve) => {
    rl.question(`${prompt}${def}: `, (answer) => {
      rl.close();
      resolve((answer ?? defaultValue).trim() || defaultValue);
    });
  });
}

async function checkPython() {
  const py = process.env.OPENVIKING_PYTHON || (IS_WIN ? "python" : "python3");
  const r = await runCapture(py, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]);
  if (r.code !== 0 || !r.out) {
    return { ok: false, detail: tr("Python not found or failed. Install Python >= 3.10.", "Python 未找到或执行失败，请安装 Python >= 3.10"), cmd: py };
  }
  const [major, minor] = r.out.split(".").map(Number);
  if (major < 3 || (major === 3 && minor < 10)) {
    return { ok: false, detail: tr(`Python ${r.out} is too old. Need >= 3.10.`, `Python ${r.out} 版本过低，需要 >= 3.10`), cmd: py };
  }
  return { ok: true, detail: r.out, cmd: py };
}

async function checkNode() {
  const r = await runCapture("node", ["-v"]);
  if (r.code !== 0 || !r.out) {
    return { ok: false, detail: tr("Node.js not found. Install Node.js >= 22.", "Node.js 未找到，请安装 Node.js >= 22") };
  }
  const v = r.out.replace(/^v/, "").split(".")[0];
  const major = parseInt(v, 10);
  if (isNaN(major) || major < 22) {
    return { ok: false, detail: tr(`Node.js ${r.out} is too old. Need >= 22.`, `Node.js ${r.out} 版本过低，需要 >= 22`) };
  }
  return { ok: true, detail: r.out };
}

async function validateEnvironment() {
  info(tr("Checking OpenViking runtime environment...", "正在校验 OpenViking 运行环境..."));
  console.log("");

  const missing = [];
  const py = await checkPython();
  if (py.ok) {
    info(`  Python: ${py.detail} ✓`);
  } else {
    missing.push(`Python 3.10+ | ${py.detail}`);
  }

  const node = await checkNode();
  if (node.ok) {
    info(`  Node.js: ${node.detail} ✓`);
  } else {
    missing.push(`Node.js 22+ | ${node.detail}`);
  }

  if (missing.length > 0) {
    console.log("");
    err(tr("Environment check failed. Install missing dependencies first.", "环境校验未通过，请先安装以下缺失组件。"));
    console.log("");
    if (missing.some((m) => m.startsWith("Python"))) {
      console.log(tr("Python (example):", "Python（示例）："));
      if (IS_WIN) console.log("  winget install --id Python.Python.3.11 -e");
      else console.log("  pyenv install 3.11.12 && pyenv global 3.11.12");
      console.log("");
    }
    if (missing.some((m) => m.startsWith("Node"))) {
      console.log(tr("Node.js (example):", "Node.js（示例）："));
      if (IS_WIN) console.log("  nvm install 22.22.0 && nvm use 22.22.0");
      else console.log("  nvm install 22 && nvm use 22");
      console.log("");
    }
    process.exit(1);
  }
  console.log("");
  info(tr("Environment check passed ✓", "环境校验通过 ✓"));
  console.log("");
}

async function checkOpenClaw() {
  if (process.env.SKIP_OPENCLAW === "1") {
    info(tr("Skipping OpenClaw check (SKIP_OPENCLAW=1)", "跳过 OpenClaw 校验 (SKIP_OPENCLAW=1)"));
    return;
  }
  info(tr("Checking OpenClaw...", "正在校验 OpenClaw..."));
  // On Windows, spawn without shell may not resolve openclaw.cmd; use shell so PATH and .cmd work
  const r = await runCapture("openclaw", ["--version"], { shell: IS_WIN });
  if (r.code === 0) {
    info(tr("OpenClaw detected ✓", "OpenClaw 已安装 ✓"));
    return;
  }
  err(tr("OpenClaw not found. Install it manually, then rerun this script.", "未检测到 OpenClaw，请先手动安装后再执行本脚本"));
  console.log("");
  console.log(tr("Recommended command:", "推荐命令："));
  console.log(`  npm install -g openclaw --registry ${NPM_REGISTRY}`);
  console.log("");
  console.log("  openclaw --version");
  console.log("  openclaw onboard");
  console.log("");
  process.exit(1);
}

let openvikingPythonPath = "";

async function installOpenViking() {
  if (process.env.SKIP_OPENVIKING === "1") {
    info(tr("Skipping OpenViking install (SKIP_OPENVIKING=1)", "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)"));
    return;
  }

  const py = (await checkPython()).cmd;
  if (!py) {
    err(tr("Python check failed.", "Python 校验失败"));
    process.exit(1);
  }

  // Source install: only when repo path is explicitly set (default off)
  if (openvikingRepo && existsSync(join(openvikingRepo, "pyproject.toml"))) {
    info(tr(`Installing OpenViking from source (editable): ${openvikingRepo}`, `正在从源码安装 OpenViking（可编辑）: ${openvikingRepo}`));
    try {
      await run(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { silent: true });
      await run(py, ["-m", "pip", "install", "-e", openvikingRepo]);
      openvikingPythonPath = py;
      info(tr("OpenViking installed ✓ (source)", "OpenViking 安装完成 ✓（源码）"));
      return;
    } catch (e) {
      err(tr("OpenViking source install failed.", "OpenViking 源码安装失败"));
      throw e;
    }
  }

  info(tr("Installing OpenViking from PyPI...", "正在安装 OpenViking (PyPI)..."));
  if (openvikingVersion) {
    info(tr(`Requested version: openviking==${openvikingVersion}`, `指定版本: openviking==${openvikingVersion}`));
  } else {
    info(tr("Requested version: latest", "指定版本: 最新"));
  }
  info(tr(`Using pip index: ${PIP_INDEX_URL}`, `使用 pip 镜像源: ${PIP_INDEX_URL}`));

  try {
    await run(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { silent: true });
    await run(py, ["-m", "pip", "install", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL]);
    openvikingPythonPath = py;
    info(tr("OpenViking installed ✓", "OpenViking 安装完成 ✓"));
    return;
  } catch (e) {
    // On Linux: PEP 668 externally-managed-environment → try venv
    if (!IS_WIN && (String(e).includes("externally") || String(e).includes("No module named pip"))) {
      const venvDir = join(OPENVIKING_DIR, "venv");
      const venvPy = IS_WIN ? join(venvDir, "Scripts", "python.exe") : join(venvDir, "bin", "python");
      if (existsSync(venvPy)) {
        try {
          await run(venvPy, ["-c", "import openviking"]);
          await run(venvPy, ["-m", "pip", "install", "-q", "-U", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL], { silent: true });
          openvikingPythonPath = venvPy;
          info(tr("OpenViking installed ✓ (venv)", "OpenViking 安装完成 ✓（虚拟环境）"));
          return;
        } catch (_) {}
      }
      await mkdir(OPENVIKING_DIR, { recursive: true });
      try {
        await run(py, ["-m", "venv", venvDir]);
      } catch (_) {
        err(tr("Could not create venv. Install python3-venv or use OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1", "无法创建虚拟环境，请安装 python3-venv 或设置 OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1"));
        process.exit(1);
      }
      await run(venvPy, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", PIP_INDEX_URL], { silent: true });
      await run(venvPy, ["-m", "pip", "install", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL]);
      openvikingPythonPath = venvPy;
      info(tr("OpenViking installed ✓ (venv)", "OpenViking 安装完成 ✓（虚拟环境）"));
      return;
    }
    if (process.env.OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES === "1") {
      try {
        await run(py, ["-m", "pip", "install", "--break-system-packages", OPENVIKING_PIP_SPEC, "-i", PIP_INDEX_URL]);
        openvikingPythonPath = py;
        info(tr("OpenViking installed ✓ (system)", "OpenViking 安装完成 ✓（系统）"));
        return;
      } catch (_) {}
    }
    err(tr("OpenViking install failed. Check Python >= 3.10 and pip.", "OpenViking 安装失败，请检查 Python >= 3.10 及 pip"));
    throw e;
  }
}

let selectedServerPort = DEFAULT_SERVER_PORT;

async function configureOvConf() {
  await mkdir(OPENVIKING_DIR, { recursive: true });
  let workspace = join(OPENVIKING_DIR, "data");
  let serverPort = String(DEFAULT_SERVER_PORT);
  let agfsPort = String(DEFAULT_AGFS_PORT);
  let vlmModel = DEFAULT_VLM_MODEL;
  let embeddingModel = DEFAULT_EMBED_MODEL;
  let vlmApiKey = process.env.OPENVIKING_VLM_API_KEY || process.env.OPENVIKING_ARK_API_KEY || "";
  let embeddingApiKey = process.env.OPENVIKING_EMBEDDING_API_KEY || process.env.OPENVIKING_ARK_API_KEY || "";

  if (!installYes) {
    console.log("");
    workspace = await question(tr("OpenViking workspace path", "OpenViking 数据目录"), workspace);
    serverPort = await question(tr("OpenViking HTTP port", "OpenViking HTTP 端口"), serverPort);
    agfsPort = await question(tr("AGFS port", "AGFS 端口"), agfsPort);
    vlmModel = await question(tr("VLM model", "VLM 模型"), vlmModel);
    embeddingModel = await question(tr("Embedding model", "Embedding 模型"), embeddingModel);
    console.log(tr("VLM and Embedding API keys can differ. Leave empty to edit ov.conf later.", "说明：VLM 与 Embedding 的 API Key 可分别填写，留空可稍后在 ov.conf 修改。"));
    const vlmInput = await question(tr("VLM API key (optional)", "VLM API Key（可留空）"), "");
    const embInput = await question(tr("Embedding API key (optional)", "Embedding API Key（可留空）"), "");
    if (vlmInput) vlmApiKey = vlmInput;
    if (embInput) embeddingApiKey = embInput;
  }

  selectedServerPort = parseInt(serverPort, 10) || DEFAULT_SERVER_PORT;
  const agfsPortNum = parseInt(agfsPort, 10) || DEFAULT_AGFS_PORT;
  await mkdir(workspace, { recursive: true });

  const cfg = {
    server: {
      host: "127.0.0.1",
      port: selectedServerPort,
      root_api_key: null,
      cors_origins: ["*"],
    },
    storage: {
      workspace,
      vectordb: { name: "context", backend: "local", project: "default" },
      agfs: { port: agfsPortNum, log_level: "warn", backend: "local", timeout: 10, retry_times: 3 },
    },
    embedding: {
      dense: {
        backend: "volcengine",
        api_key: embeddingApiKey || null,
        model: embeddingModel,
        api_base: "https://ark.cn-beijing.volces.com/api/v3",
        dimension: 1024,
        input: "multimodal",
      },
    },
    vlm: {
      backend: "volcengine",
      api_key: vlmApiKey || null,
      model: vlmModel,
      api_base: "https://ark.cn-beijing.volces.com/api/v3",
      temperature: 0.1,
      max_retries: 3,
    },
  };

  const confPath = join(OPENVIKING_DIR, "ov.conf");
  await writeFile(confPath, JSON.stringify(cfg, null, 2), "utf8");
  info(tr(`Config generated: ${confPath}`, `已生成配置: ${confPath}`));
}

async function downloadPlugin() {
  await mkdir(PLUGIN_DEST, { recursive: true });
  info(tr(`Downloading memory-openviking plugin from ${REPO}@${BRANCH}...`, `正在从 ${REPO}@${BRANCH} 下载 memory-openviking 插件...`));
  const maxRetries = 3;
  for (let i = 0; i < PLUGIN_FILES.length; i++) {
    const rel = PLUGIN_FILES[i];
    const name = rel.split("/").pop();
    process.stdout.write(`  [${i + 1}/${PLUGIN_FILES.length}] ${name} `);
    const url = `${GH_RAW}/${rel}`;
    let ok = false;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const res = await fetch(url);
        if (res.ok) {
          const buf = await res.arrayBuffer();
          await writeFile(join(PLUGIN_DEST, name), Buffer.from(buf), "utf8");
          ok = true;
          break;
        }
      } catch (_) {}
      if (attempt < maxRetries) await new Promise((r) => setTimeout(r, 2000));
    }
    if (ok) {
      console.log("✓");
    } else if (name === ".gitignore") {
      console.log(tr("(retries failed, using minimal .gitignore)", "（重试失败，使用最小 .gitignore）"));
      await writeFile(join(PLUGIN_DEST, name), "node_modules/\n", "utf8");
    } else {
      console.log("");
      err(tr(`Download failed after ${maxRetries} retries: ${url}`, `下载失败（已重试 ${maxRetries} 次）: ${url}`));
      process.exit(1);
    }
  }
  info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
  try {
    await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: PLUGIN_DEST, silent: false });
  } catch (e) {
    err(tr(`Plugin dependency install failed: ${PLUGIN_DEST}`, `插件依赖安装失败: ${PLUGIN_DEST}`));
    throw e;
  }
  info(tr(`Plugin deployed: ${PLUGIN_DEST}`, `插件部署完成: ${PLUGIN_DEST}`));
}

async function configureOpenClawPlugin(pluginPath = PLUGIN_DEST) {
  info(tr("Configuring OpenClaw plugin...", "正在配置 OpenClaw 插件..."));
  const cfgPath = join(OPENCLAW_DIR, "openclaw.json");
  let cfg = {};
  if (existsSync(cfgPath)) {
    try {
      const raw = await readFile(cfgPath, "utf8");
      if (raw.trim()) cfg = JSON.parse(raw);
    } catch (_) {
      warn(tr("Existing openclaw.json invalid. Rebuilding required sections.", "已有 openclaw.json 非法，将重建相关配置节点。"));
    }
  }

  if (!cfg.plugins) cfg.plugins = {};
  if (!cfg.gateway) cfg.gateway = {};
  if (!cfg.plugins.slots) cfg.plugins.slots = {};
  if (!cfg.plugins.load) cfg.plugins.load = {};
  if (!cfg.plugins.entries) cfg.plugins.entries = {};

  const existingPaths = Array.isArray(cfg.plugins.load.paths) ? cfg.plugins.load.paths : [];
  const mergedPaths = [...new Set([...existingPaths, pluginPath])];
  const ovConfPath = join(OPENVIKING_DIR, "ov.conf");

  cfg.plugins.enabled = true;
  cfg.plugins.allow = ["memory-openviking"];
  cfg.plugins.slots.memory = "memory-openviking";
  cfg.plugins.load.paths = mergedPaths;
  cfg.plugins.entries["memory-openviking"] = {
    config: {
      mode: "local",
      configPath: ovConfPath,
      port: selectedServerPort,
      targetUri: "viking://user/memories",
      autoRecall: true,
      autoCapture: true,
    },
  };
  cfg.gateway.mode = "local";

  await mkdir(OPENCLAW_DIR, { recursive: true });
  await writeFile(cfgPath, JSON.stringify(cfg, null, 2) + "\n", "utf8");
  info(tr("OpenClaw plugin configured", "OpenClaw 插件配置完成"));
}

async function writeOpenvikingEnv() {
  let pyPath = openvikingPythonPath;
  if (!pyPath) {
    const py = (await checkPython()).cmd;
    if (IS_WIN) {
      const r = await runCapture("where", [py], { shell: true });
      pyPath = r.out.split(/\r?\n/)[0]?.trim() || py;
    } else {
      const r = await runCapture("which", [py]);
      pyPath = r.out.trim() || py;
    }
  }
  await mkdir(OPENCLAW_DIR, { recursive: true });
  const envContent = IS_WIN
    ? `@echo off\nset "OPENVIKING_PYTHON=${pyPath.replace(/"/g, '""')}"`
    : `export OPENVIKING_PYTHON='${pyPath.replace(/'/g, "'\"'\"'")}'`;
  const envFile = IS_WIN ? join(OPENCLAW_DIR, "openviking.env.bat") : join(OPENCLAW_DIR, "openviking.env");
  await writeFile(envFile, envContent + "\n", "utf8");
  if (IS_WIN) {
    const ps1Path = join(OPENCLAW_DIR, "openviking.env.ps1");
    await writeFile(ps1Path, `$env:OPENVIKING_PYTHON = "${String(pyPath).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"\n`, "utf8");
    info(tr(`Environment file generated: ${ps1Path}`, `已生成环境文件: ${ps1Path}`));
  } else {
    info(tr(`Environment file generated: ${envFile}`, `已生成环境文件: ${envFile}`));
  }
}

async function main() {
  console.log("");
  bold(tr("🦣 OpenClaw + OpenViking Installer", "🦣 OpenClaw + OpenViking 一键安装"));
  console.log("");

  await validateEnvironment();
  await checkOpenClaw();
  await installOpenViking();
  await configureOvConf();

  let pluginPath;
  const localPluginDir = openvikingRepo ? join(openvikingRepo, "examples", "openclaw-memory-plugin") : "";
  if (openvikingRepo && localPluginDir && existsSync(join(localPluginDir, "index.ts"))) {
    pluginPath = localPluginDir;
    info(tr(`Using local plugin from repo: ${pluginPath}`, `使用仓库内插件: ${pluginPath}`));
    if (!existsSync(join(pluginPath, "node_modules"))) {
      info(tr("Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
      await run("npm", ["install", "--no-audit", "--no-fund"], { cwd: pluginPath, silent: false });
    }
  } else {
    await downloadPlugin();
    pluginPath = PLUGIN_DEST;
  }
  await configureOpenClawPlugin(pluginPath);
  await writeOpenvikingEnv();

  console.log("");
  bold("═══════════════════════════════════════════════════════════");
  bold("  " + tr("Installation complete!", "安装完成！"));
  bold("═══════════════════════════════════════════════════════════");
  console.log("");
  info(tr("Run these commands to start OpenClaw + OpenViking:", "请按以下命令启动 OpenClaw + OpenViking："));
  console.log("  1) openclaw --version");
  console.log("  2) openclaw onboard");
  if (IS_WIN) {
    console.log('  3) call "%USERPROFILE%\\.openclaw\\openviking.env.bat" && openclaw gateway');
  } else {
    console.log("  3) source ~/.openclaw/openviking.env && openclaw gateway");
  }
  console.log("  4) openclaw status");
  console.log("");
  info(tr(`You can edit the config freely: ${OPENVIKING_DIR}/ov.conf`, `你可以按需自由修改配置文件: ${OPENVIKING_DIR}/ov.conf`));
  console.log("");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
