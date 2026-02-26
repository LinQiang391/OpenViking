#!/usr/bin/env node
/**
 * OpenClaw + OpenViking setup helper
 * Usage: npx openclaw-openviking-setup-helper
 * Or: npx openclaw-openviking-setup-helper --help
 *
 * Features: env check, install openviking/openclaw, configure memory-openviking plugin
 */

import { spawn } from "node:child_process";
import { mkdir, writeFile, access, readFile, rm } from "node:fs/promises";
import { createInterface } from "node:readline";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const GITHUB_RAW =
  process.env.OPENVIKING_GITHUB_RAW ||
  "https://raw.githubusercontent.com/OpenViking/OpenViking/main";

const IS_WIN = process.platform === "win32";
const HOME = process.env.HOME || process.env.USERPROFILE || "";
const OPENCLAW_DIR = join(HOME, ".openclaw");
const OPENVIKING_DIR = join(HOME, ".openviking");
const EXT_DIR = join(OPENCLAW_DIR, "extensions");
const PLUGIN_DEST = join(EXT_DIR, "memory-openviking");

function log(msg, level = "info") {
  const icons = { info: "\u2139", ok: "\u2713", err: "\u2717", warn: "\u26A0" };
  const icon = icons[level] || "";
  console.log(`${icon} ${msg}`);
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
    let err = "";
    p.stdout?.on("data", (d) => (out += d));
    p.stderr?.on("data", (d) => (err += d));
    p.on("error", (e) => {
      if (e.code === "ENOENT") resolve({ code: -1, out: "", err: `command not found: ${cmd}` });
      else resolve({ code: -1, out: "", err: String(e) });
    });
    p.on("close", (code) => resolve({ code, out: out.trim(), err: err.trim() }));
  });
}

function runCaptureWithTimeout(cmd, args, timeoutMs, opts = {}) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: opts.shell ?? false,
      ...opts,
    });
    let out = "";
    let err = "";
    let settled = false;
    const done = (result) => { if (!settled) { settled = true; resolve(result); } };
    const timer = setTimeout(() => { p.kill(); done({ code: out ? 0 : -1, out: out.trim(), err: err.trim() }); }, timeoutMs);
    p.stdout?.on("data", (d) => (out += d));
    p.stderr?.on("data", (d) => (err += d));
    p.on("error", (e) => { clearTimeout(timer); done({ code: -1, out: "", err: String(e) }); });
    p.on("close", (code) => { clearTimeout(timer); done({ code, out: out.trim(), err: err.trim() }); });
  });
}

async function question(prompt, defaultValue = "") {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const def = defaultValue ? ` [${defaultValue}]` : "";
  return new Promise((resolve) => {
    rl.question(`${prompt}${def}: `, (answer) => {
      rl.close();
      resolve((answer ?? defaultValue).trim());
    });
  });
}

/** Prompt for API Key (plain text input) */
async function questionApiKey(prompt) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(prompt, (answer) => {
      rl.close();
      resolve((answer ?? "").trim());
    });
  });
}

const DEFAULT_PYTHON = IS_WIN ? "python" : "python3";

async function checkPython() {
  const py = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  const { code, out } = await runCapture(py, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]);
  if (code !== 0) return { ok: false, msg: `Python not found (${py})` };
  const [major, minor] = out.split(".").map(Number);
  if (major < 3 || (major === 3 && minor < 10))
    return { ok: false, msg: `Python version too old: ${out}, need >= 3.10` };
  return { ok: true, msg: `${out} (${py})` };
}

async function checkOpenvikingModule() {
  const py = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  const { code } = await runCapture(py, ["-c", "import openviking"]);
  return code === 0 ? { ok: true } : { ok: false };
}

async function checkGo() {
  const goDir = process.env.OPENVIKING_GO_PATH?.replace(/^~/, HOME);
  const goCmd = goDir ? join(goDir, "go") : "go";
  const { code, out } = await runCapture(goCmd, ["version"]);
  if (code !== 0) {
    return {
      ok: false,
      msg: "Go not found. Please install Go >= 1.22 and add to PATH, or set OPENVIKING_GO_PATH to Go bin dir (e.g. ~/local/go/bin)",
    };
  }
  const m = out.match(/go([0-9]+)\.([0-9]+)/);
  if (!m) return { ok: false, msg: "Cannot parse Go version" };
  const [, major, minor] = m.map(Number);
  if (major < 1 || (major === 1 && minor < 22))
    return { ok: false, msg: `Go version too old, need >= 1.22` };
  return { ok: true, msg: `${major}.${minor}` };
}

async function checkOvvConf() {
  const cfg = process.env.OPENVIKING_CONFIG_FILE || join(OPENVIKING_DIR, "ov.conf");
  try {
    await access(cfg);
    return { ok: true, path: cfg };
  } catch {
    return { ok: false, path: cfg };
  }
}

async function checkOpenclaw() {
  if (IS_WIN) {
    const { code } = await runCaptureWithTimeout("openclaw", ["--version"], 10000, { shell: true });
    return code === 0 ? { ok: true } : { ok: false };
  }
  const { code } = await runCapture("openclaw", ["--version"]);
  return code === 0 ? { ok: true } : { ok: false };
}

const DEFAULT_SERVER_PORT = 1933;
const DEFAULT_AGFS_PORT = 1833;
const DEFAULT_VLM_MODEL = "doubao-seed-1-8-251228";

function buildOvvConfJson(opts = {}) {
  const { apiKey = "", serverPort = DEFAULT_SERVER_PORT, agfsPort = DEFAULT_AGFS_PORT, vlmModel = DEFAULT_VLM_MODEL } = opts;
  const workspace = join(HOME, ".openviking", "data");
  const cfg = {
    server: {
      host: "127.0.0.1",
      port: serverPort,
      root_api_key: null,
      cors_origins: ["*"],
    },
    storage: {
      workspace,
      vectordb: {
        name: "context",
        backend: "local",
        project: "default",
      },
      agfs: {
        port: agfsPort,
        log_level: "warn",
        backend: "local",
        timeout: 10,
        retry_times: 3,
      },
    },
    embedding: {
      dense: {
        backend: "volcengine",
        api_key: apiKey || null,
        model: "doubao-embedding-vision-250615",
        api_base: "https://ark.cn-beijing.volces.com/api/v3",
        dimension: 1024,
        input: "multimodal",
      },
    },
    vlm: {
      backend: "volcengine",
      api_key: apiKey || null,
      model: vlmModel,
      api_base: "https://ark.cn-beijing.volces.com/api/v3",
      temperature: 0.1,
      max_retries: 3,
    },
  };
  return JSON.stringify(cfg, null, 2);
}

function parsePort(val, defaultVal) {
  const n = parseInt(val, 10);
  return Number.isFinite(n) && n >= 1 && n <= 65535 ? n : defaultVal;
}

async function ensureOvvConf(cfgPath, opts = {}) {
  const dir = dirname(cfgPath);
  await mkdir(dir, { recursive: true });
  await writeFile(cfgPath, buildOvvConfJson(opts));
  log(`Created config: ${cfgPath}`, "ok");
  if (!opts.apiKey) {
    log("embedding api_key not set; memory search may be unavailable. Edit ov.conf to add later.", "warn");
  }
}

async function getApiKeyFromOvvConf(cfgPath) {
  let raw;
  try {
    raw = await readFile(cfgPath, "utf-8");
    const cfg = JSON.parse(raw);
    return cfg?.embedding?.dense?.api_key || "";
  } catch {
    const m = raw?.match(/api_key\s*:\s*["']?([^"'\s#]+)["']?/);
    return m ? m[1].trim() : "";
  }
}

async function getOvvConfPorts(cfgPath) {
  try {
    const raw = await readFile(cfgPath, "utf-8");
    const cfg = JSON.parse(raw);
    return {
      serverPort: cfg?.server?.port ?? DEFAULT_SERVER_PORT,
      agfsPort: cfg?.storage?.agfs?.port ?? DEFAULT_AGFS_PORT,
    };
  } catch {
    return { serverPort: DEFAULT_SERVER_PORT, agfsPort: DEFAULT_AGFS_PORT };
  }
}

async function isOvvConfInvalid(cfgPath) {
  try {
    const raw = await readFile(cfgPath, "utf-8");
    JSON.parse(raw);
    return false;
  } catch {
    return true;
  }
}

async function updateOvvConf(cfgPath, opts = {}) {
  let cfg;
  try {
    const raw = await readFile(cfgPath, "utf-8");
    cfg = JSON.parse(raw);
  } catch {
    log("ov.conf is not valid JSON, will create new JSON config", "warn");
    await ensureOvvConf(cfgPath, opts);
    return;
  }
  if (opts.apiKey !== undefined) {
    if (!cfg.embedding) cfg.embedding = {};
    if (!cfg.embedding.dense) cfg.embedding.dense = {};
    cfg.embedding.dense.api_key = opts.apiKey || null;
    if (!cfg.vlm) cfg.vlm = {};
    cfg.vlm.api_key = opts.apiKey || null;
  }
  if (opts.vlmModel !== undefined) {
    if (!cfg.vlm) cfg.vlm = {};
    cfg.vlm.model = opts.vlmModel;
    if (!cfg.vlm.api_base) cfg.vlm.api_base = "https://ark.cn-beijing.volces.com/api/v3";
    if (!cfg.vlm.backend) cfg.vlm.backend = "volcengine";
  }
  if (opts.serverPort !== undefined && cfg.server) cfg.server.port = opts.serverPort;
  if (opts.agfsPort !== undefined && cfg.storage?.agfs) cfg.storage.agfs.port = opts.agfsPort;
  await writeFile(cfgPath, JSON.stringify(cfg, null, 2));
}

async function collectOvvConfInteractive(nonInteractive) {
  const opts = {
    apiKey: process.env.OPENVIKING_ARK_API_KEY || "",
    serverPort: DEFAULT_SERVER_PORT,
    agfsPort: DEFAULT_AGFS_PORT,
    vlmModel: DEFAULT_VLM_MODEL,
  };
  if (nonInteractive) return opts;
  console.log("\n--- ov.conf setup ---");
  console.log("Memory search requires Volcengine Ark Embedding API Key (Doubao platform)");
  console.log("Memory extraction also requires a VLM (LLM) model for analyzing conversations.");
  console.log("Get API Key at: https://console.volcengine.com/ark");
  opts.apiKey = (await questionApiKey("API Key (leave blank to skip, edit ov.conf later): ")) || opts.apiKey;
  const vlmModelStr = await question(`VLM model for memory extraction [${DEFAULT_VLM_MODEL}]`, DEFAULT_VLM_MODEL);
  opts.vlmModel = vlmModelStr || DEFAULT_VLM_MODEL;
  const serverPortStr = await question(`OpenViking HTTP port [${DEFAULT_SERVER_PORT}]`, String(DEFAULT_SERVER_PORT));
  opts.serverPort = parsePort(serverPortStr, DEFAULT_SERVER_PORT);
  const agfsPortStr = await question(`AGFS port [${DEFAULT_AGFS_PORT}]`, String(DEFAULT_AGFS_PORT));
  opts.agfsPort = parsePort(agfsPortStr, DEFAULT_AGFS_PORT);
  return opts;
}

async function installOpenviking(repoRoot) {
  const py = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  log(`Installing openviking (using ${py})...`);
  if (repoRoot && existsSync(join(repoRoot, "pyproject.toml"))) {
    await run(py, ["-m", "pip", "install", "-e", repoRoot]);
    return;
  }
  await run(py, ["-m", "pip", "install", "openviking"]);
}

async function fetchPluginFromGitHub(dest) {
  log("Downloading memory-openviking plugin from GitHub...");
  const files = [
    "examples/openclaw-memory-plugin/index.ts",
    "examples/openclaw-memory-plugin/config.ts",
    "examples/openclaw-memory-plugin/openclaw.plugin.json",
    "examples/openclaw-memory-plugin/package.json",
    "examples/openclaw-memory-plugin/package-lock.json",
    "examples/openclaw-memory-plugin/.gitignore",
  ];
  await mkdir(dest, { recursive: true });
  for (let i = 0; i < files.length; i++) {
    const rel = files[i];
    const name = rel.split("/").pop();
    process.stdout.write(`  Downloading ${i + 1}/${files.length}: ${name} ... `);
    const url = `${GITHUB_RAW}/${rel}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Download failed: ${url}`);
    const buf = await res.arrayBuffer();
    await writeFile(join(dest, name), Buffer.from(buf));
    process.stdout.write("\u2713\n");
  }
  log(`Plugin downloaded to ${dest}`, "ok");
  process.stdout.write("  Installing plugin deps (npm install)... ");
  await run("npm", ["install", "--no-audit", "--no-fund"], {
    cwd: dest,
    silent: true,
  });
  process.stdout.write("\u2713\n");
  log("Plugin deps installed", "ok");
}

async function fixStalePluginPaths(pluginPath) {
  const cfgPath = join(OPENCLAW_DIR, "openclaw.json");
  if (!existsSync(cfgPath)) return;
  try {
    const cfg = JSON.parse(await readFile(cfgPath, "utf8"));
    let changed = false;
    const paths = cfg?.plugins?.load?.paths;
    if (Array.isArray(paths)) {
      const cleaned = paths.filter((p) => existsSync(p));
      if (!cleaned.includes(pluginPath)) cleaned.push(pluginPath);
      if (JSON.stringify(cleaned) !== JSON.stringify(paths)) {
        cfg.plugins.load.paths = cleaned;
        changed = true;
      }
    }
    const installs = cfg?.plugins?.installs;
    if (installs) {
      for (const [k, v] of Object.entries(installs)) {
        if (v?.installPath && !existsSync(v.installPath)) {
          delete installs[k];
          changed = true;
        }
      }
    }
    if (changed) {
      await writeFile(cfgPath, JSON.stringify(cfg, null, 2) + "\n");
      log("Cleaned stale plugin paths from openclaw.json", "ok");
    }
  } catch {}
}

async function configureOpenclawViaJson(pluginPath, serverPort) {
  const cfgPath = join(OPENCLAW_DIR, "openclaw.json");
  let cfg = {};
  try { cfg = JSON.parse(await readFile(cfgPath, "utf8")); } catch { /* start fresh */ }
  if (!cfg.plugins) cfg.plugins = {};
  cfg.plugins.enabled = true;
  cfg.plugins.allow = ["memory-openviking"];
  if (!cfg.plugins.slots) cfg.plugins.slots = {};
  cfg.plugins.slots.memory = "memory-openviking";
  if (!cfg.plugins.load) cfg.plugins.load = {};
  const paths = Array.isArray(cfg.plugins.load.paths) ? cfg.plugins.load.paths : [];
  if (!paths.includes(pluginPath)) paths.push(pluginPath);
  cfg.plugins.load.paths = paths;
  if (!cfg.plugins.entries) cfg.plugins.entries = {};
  cfg.plugins.entries["memory-openviking"] = {
    config: {
      mode: "local",
      configPath: "~/.openviking/ov.conf",
      port: serverPort,
      targetUri: "viking://",
      autoRecall: true,
      autoCapture: true,
    },
  };
  if (!cfg.gateway) cfg.gateway = {};
  cfg.gateway.mode = "local";
  await mkdir(OPENCLAW_DIR, { recursive: true });
  await writeFile(cfgPath, JSON.stringify(cfg, null, 2) + "\n");
}

async function configureOpenclawViaCli(pluginPath, serverPort, mode) {
  const runNoShell = (cmd, args, opts = {}) =>
    run(cmd, args, { ...opts, shell: false });
  if (mode === "link") {
    if (existsSync(PLUGIN_DEST)) {
      log(`Removing old plugin dir ${PLUGIN_DEST}...`, "info");
      await rm(PLUGIN_DEST, { recursive: true, force: true });
    }
    await run("openclaw", ["plugins", "install", "-l", pluginPath]);
  } else {
    await runNoShell("openclaw", ["config", "set", "plugins.load.paths", JSON.stringify([pluginPath])], { silent: true }).catch(() => {});
  }
  await runNoShell("openclaw", ["config", "set", "plugins.enabled", "true"]);
  await runNoShell("openclaw", ["config", "set", "plugins.allow", JSON.stringify(["memory-openviking"]), "--json"]);
  await runNoShell("openclaw", ["config", "set", "gateway.mode", "local"]);
  await runNoShell("openclaw", ["config", "set", "plugins.slots.memory", "memory-openviking"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.mode", "local"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.configPath", "~/.openviking/ov.conf"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.port", String(serverPort)]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.targetUri", "viking://"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.autoRecall", "true", "--json"]);
  await runNoShell("openclaw", ["config", "set", "plugins.entries.memory-openviking.config.autoCapture", "true", "--json"]);
}

async function configureOpenclaw(pluginPath, serverPort = DEFAULT_SERVER_PORT, mode = "link") {
  await fixStalePluginPaths(pluginPath);
  if (IS_WIN) {
    await configureOpenclawViaJson(pluginPath, serverPort);
  } else {
    await configureOpenclawViaCli(pluginPath, serverPort, mode);
  }
  log("OpenClaw plugin config done", "ok");
}

async function resolveCommand(cmd) {
  if (IS_WIN) {
    const { code, out } = await runCapture("where", [cmd], { shell: true });
    return code === 0 ? out.split(/\r?\n/)[0].trim() : "";
  }
  const { out } = await runCapture("sh", ["-c", `command -v ${cmd} 2>/dev/null || which ${cmd}`]);
  return out || "";
}

async function writeOpenvikingEnv() {
  const pyCmd = process.env.OPENVIKING_PYTHON || DEFAULT_PYTHON;
  const pyPath = await resolveCommand(pyCmd);
  const goOut = await resolveCommand("go");
  const goPath = goOut ? dirname(goOut) : "";
  await mkdir(OPENCLAW_DIR, { recursive: true });

  if (IS_WIN) {
    const lines = [];
    if (pyPath) lines.push(`set OPENVIKING_PYTHON=${pyPath}`);
    if (goPath) lines.push(`set OPENVIKING_GO_PATH=${goPath}`);
    if (process.env.GOPATH) lines.push(`set OPENVIKING_GOPATH=${process.env.GOPATH}`);
    if (process.env.GOPROXY) lines.push(`set OPENVIKING_GOPROXY=${process.env.GOPROXY}`);
    await writeFile(join(OPENCLAW_DIR, "openviking.env.bat"), lines.join("\r\n") + "\r\n");
    log(`Written ~/.openclaw/openviking.env.bat`, "ok");
  } else {
    const lines = [];
    if (pyPath) lines.push(`export OPENVIKING_PYTHON='${pyPath}'`);
    if (goPath) lines.push(`export OPENVIKING_GO_PATH='${goPath}'`);
    if (process.env.GOPATH) lines.push(`export OPENVIKING_GOPATH='${process.env.GOPATH}'`);
    if (process.env.GOPROXY) lines.push(`export OPENVIKING_GOPROXY='${process.env.GOPROXY}'`);
    await writeFile(join(OPENCLAW_DIR, "openviking.env"), lines.join("\n") + "\n");
    log(`Written ~/.openclaw/openviking.env`, "ok");
  }
}

async function main() {
  const args = process.argv.slice(2);
  const help = args.includes("--help") || args.includes("-h");
  const nonInteractive = args.includes("--yes") || args.includes("-y");

  if (help) {
    console.log(`
OpenClaw + OpenViking setup helper

Usage: npx openclaw-openviking-setup-helper [options]

Options:
  -y, --yes     Non-interactive, use defaults
  -h, --help    Show help

Steps:
  1. Check Python (>=3.10), openviking module, ov.conf, Go (optional)
  2. Prompt to install openviking / create ov.conf if missing
  3. Check OpenClaw (must be installed separately: npm i -g openclaw)
  4. Download or link memory-openviking plugin
  5. Configure OpenClaw to use the plugin
  6. Write ~/.openclaw/openviking.env

Env vars:
  OPENVIKING_PYTHON       Python path
  OPENVIKING_CONFIG_FILE  ov.conf path
  OPENVIKING_REPO         Local OpenViking repo path (use local plugin if set)
  OPENVIKING_ARK_API_KEY  Volcengine Ark API Key (used in -y mode, skip prompt)
  OPENVIKING_GO_PATH      Go bin dir (when Go not in PATH, e.g. ~/local/go/bin)
`);
    process.exit(0);
  }

  console.log("\n\ud83e\udd9e OpenClaw + OpenViking setup helper\n");

  // 1. Env check
  log("Checking environment...");
  const pyResult = await checkPython();
  if (!pyResult.ok) {
    log(pyResult.msg, "err");
    log("Please install Python >= 3.10: https://www.python.org/", "err");
    process.exit(1);
  }
  log(`Python: ${pyResult.msg}`, "ok");

  const ovMod = await checkOpenvikingModule();
  if (!ovMod.ok) {
    const repo = process.env.OPENVIKING_REPO;
    const doInstall = nonInteractive || (await question("openviking module not found, install? (y/n)", "y")).toLowerCase() === "y";
    if (doInstall) await installOpenviking(repo);
    else {
      log("Please install: pip install openviking or pip install -e . in OpenViking repo", "err");
      process.exit(1);
    }
  } else {
    log("openviking module: installed", "ok");
  }

  const goResult = await checkGo();
  if (!goResult.ok) {
    log(`Go: not found (optional - only needed if AGFS backend is not "local")`, "warn");
  } else {
    log(`Go: ${goResult.msg}`, "ok");
  }

  const ovConf = await checkOvvConf();
  const ovConfPath = ovConf.path;
  let ovOpts = { apiKey: process.env.OPENVIKING_ARK_API_KEY || "", serverPort: DEFAULT_SERVER_PORT, agfsPort: DEFAULT_AGFS_PORT };
  if (!ovConf.ok) {
    const create = nonInteractive || (await question(`ov.conf not found (${ovConfPath}), create? (y/n)`, "y")).toLowerCase() === "y";
    if (create) {
      ovOpts = await collectOvvConfInteractive(nonInteractive);
      await ensureOvvConf(ovConfPath, ovOpts);
    } else {
      log("Please create ~/.openviking/ov.conf manually", "err");
      process.exit(1);
    }
  } else {
    log(`ov.conf: ${ovConfPath}`, "ok");
    const invalid = await isOvvConfInvalid(ovConfPath);
    const existingKey = await getApiKeyFromOvvConf(ovConfPath);
    const existingPorts = await getOvvConfPorts(ovConfPath);
    if (invalid) {
      ovOpts = await collectOvvConfInteractive(nonInteractive);
      await ensureOvvConf(ovConfPath, ovOpts);
      log("Converted ov.conf to JSON format", "ok");
    } else if (!existingKey && !nonInteractive) {
      ovOpts = { ...existingPorts, apiKey: (await questionApiKey("\nembedding API Key not set. Enter Volcengine Ark API Key (leave blank to skip): ")) || process.env.OPENVIKING_ARK_API_KEY || "" };
      if (ovOpts.apiKey) {
        await updateOvvConf(ovConfPath, { apiKey: ovOpts.apiKey });
        log("Written API Key to ov.conf", "ok");
      } else {
        log("API Key not set; memory search may be unavailable", "warn");
      }
      ovOpts = { ...existingPorts, apiKey: ovOpts.apiKey };
    } else if (!existingKey && process.env.OPENVIKING_ARK_API_KEY) {
      await updateOvvConf(ovConfPath, { apiKey: process.env.OPENVIKING_ARK_API_KEY });
      log("Written API Key from env to ov.conf", "ok");
      ovOpts = { ...existingPorts, apiKey: process.env.OPENVIKING_ARK_API_KEY };
    } else {
      ovOpts = { ...existingPorts, apiKey: existingKey };
    }
  }

  // 2. OpenClaw
  const hasOpenclaw = await checkOpenclaw();
  if (!hasOpenclaw.ok) {
    log("OpenClaw not found.", "err");
    log("Please install OpenClaw first, then run this script again.", "err");
    console.log("\n  npm install -g openclaw");
    console.log("\n  Docs: https://docs.openclaw.ai/start/getting-started");
    console.log("  Source: https://github.com/openclaw/openclaw\n");
    process.exit(1);
  }
  log("OpenClaw: installed", "ok");

  // 3. Plugin
  const inferredRepoRoot = join(__dirname, "..", "..", "..");
  const repoRoot = process.env.OPENVIKING_REPO ||
    (existsSync(join(inferredRepoRoot, "examples", "openclaw-memory-plugin", "index.ts")) ? inferredRepoRoot : "");
  let pluginPath;
  if (repoRoot && existsSync(join(repoRoot, "examples", "openclaw-memory-plugin", "index.ts"))) {
    pluginPath = join(repoRoot, "examples", "openclaw-memory-plugin");
    log(`Using local plugin: ${pluginPath}`, "ok");
    if (!existsSync(join(pluginPath, "node_modules"))) {
      await run("npm", ["install", "--no-audit", "--no-fund"], {
        cwd: pluginPath,
        silent: true,
      });
    }
  } else {
    await fetchPluginFromGitHub(PLUGIN_DEST);
    pluginPath = PLUGIN_DEST;
  }

  // 4. Config
  await configureOpenclaw(pluginPath, ovOpts?.serverPort);
  await writeOpenvikingEnv();

  console.log("\n\u2705 Setup complete!\n");
  console.log("To start:");
  console.log("  openclaw gateway");
  if (IS_WIN) {
    console.log("\nOr with env vars (from OpenViking repo root):");
    console.log('  call "%USERPROFILE%\\.openclaw\\openviking.env.bat" && openclaw gateway');
  } else {
    console.log("\nOr with start script (from OpenViking repo root):");
    console.log("  source ~/.openclaw/openviking.env && openclaw gateway");
  }
  console.log("");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
