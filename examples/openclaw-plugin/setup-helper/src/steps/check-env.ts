import pc from "picocolors";
import type { InstallContext } from "../types.js";
import { runCapture, resolveAbsoluteCommand } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logError, logStep, createSpinner } from "../ui/prompts.js";

export async function checkPython(ctx: InstallContext): Promise<{
  ok: boolean;
  detail: string;
  cmd: string;
}> {
  const raw = process.env.OPENVIKING_PYTHON || (ctx.platform.isWin ? "python" : "python3");
  const py = await resolveAbsoluteCommand(raw);
  const result = await runCapture(py, [
    "-c",
    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
  ]);
  if (result.code !== 0 || !result.out) {
    return {
      ok: false,
      detail: tr(
        ctx.langZh,
        "Python not found or failed. Install Python >= 3.10.",
        "Python 未找到或执行失败，请安装 Python >= 3.10",
      ),
      cmd: py,
    };
  }
  const [major, minor] = result.out.split(".").map(Number);
  if (major < 3 || (major === 3 && minor < 10)) {
    return {
      ok: false,
      detail: tr(
        ctx.langZh,
        `Python ${result.out} is too old. Need >= 3.10.`,
        `Python ${result.out} 版本过低，需要 >= 3.10`,
      ),
      cmd: py,
    };
  }
  return { ok: true, detail: result.out, cmd: py };
}

export async function checkNode(ctx: InstallContext): Promise<{
  ok: boolean;
  detail: string;
}> {
  const result = await runCapture("node", ["-v"], { shell: ctx.platform.isWin });
  if (result.code !== 0 || !result.out) {
    return {
      ok: false,
      detail: tr(
        ctx.langZh,
        "Node.js not found. Install Node.js >= 22.",
        "Node.js 未找到，请安装 Node.js >= 22",
      ),
    };
  }
  const major = Number.parseInt(result.out.replace(/^v/, "").split(".")[0], 10);
  if (!Number.isFinite(major) || major < 22) {
    return {
      ok: false,
      detail: tr(
        ctx.langZh,
        `Node.js ${result.out} is too old. Need >= 22.`,
        `Node.js ${result.out} 版本过低，需要 >= 22`,
      ),
    };
  }
  return { ok: true, detail: result.out };
}

export async function checkOpenClaw(ctx: InstallContext): Promise<void> {
  if (process.env.SKIP_OPENCLAW === "1") {
    logInfo(
      tr(ctx.langZh, "Skipping OpenClaw check (SKIP_OPENCLAW=1)", "跳过 OpenClaw 校验 (SKIP_OPENCLAW=1)"),
    );
    return;
  }

  const result = await runCapture("openclaw", ["--version"], { shell: ctx.platform.isWin });
  if (result.code === 0) {
    logInfo(tr(ctx.langZh, "OpenClaw detected ✓", "OpenClaw 已安装 ✓"));
    return;
  }

  logError(
    tr(
      ctx.langZh,
      "OpenClaw not found. Install it manually, then rerun this script.",
      "未检测到 OpenClaw，请先手动安装后再执行本脚本",
    ),
  );
  console.log("");
  console.log(tr(ctx.langZh, "Recommended command:", "推荐命令："));
  console.log(`  npm install -g openclaw --registry ${ctx.npmRegistry}`);
  console.log("");
  console.log("  openclaw --version");
  console.log("  openclaw onboard");
  console.log("");
  process.exit(1);
}

export async function detectOpenClawVersion(ctx: InstallContext): Promise<string> {
  try {
    const result = await runCapture("openclaw", ["--version"], { shell: ctx.platform.isWin });
    if (result.code === 0 && result.out) {
      const match = result.out.match(/\d+\.\d+(\.\d+)?/);
      if (match) return match[0];
    }
  } catch {}
  return "0.0.0";
}

export async function validateEnvironment(ctx: InstallContext): Promise<{
  pythonCmd: string;
}> {
  const s = createSpinner();
  s.start(tr(ctx.langZh, "Checking environment...", "正在检查环境..."));

  const python = await checkPython(ctx);
  const node = await checkNode(ctx);

  s.stop(tr(ctx.langZh, "Environment check complete", "环境检查完成"));

  const lines: string[] = [];
  if (python.ok) {
    lines.push(`  ${pc.green("✓")} Python ${python.detail}`);
  }
  if (node.ok) {
    lines.push(`  ${pc.green("✓")} Node.js ${node.detail}`);
  }

  const missing: string[] = [];
  if (!python.ok) missing.push(python.detail);
  if (!node.ok) missing.push(node.detail);

  if (lines.length > 0) {
    logStep(lines.join("\n"));
  }

  if (missing.length > 0) {
    logError(
      tr(
        ctx.langZh,
        "Environment check failed. Install missing dependencies first.",
        "环境校验未通过，请先安装以下缺失组件。",
      ),
    );
    for (const m of missing) {
      console.log(`  ${pc.red("✗")} ${m}`);
    }
    console.log("");
    if (missing.some((item) => item.includes("Python"))) {
      console.log(tr(ctx.langZh, "Python (example):", "Python（示例）："));
      if (ctx.platform.isWin) console.log("  winget install --id Python.Python.3.11 -e");
      else console.log("  pyenv install 3.11.12 && pyenv global 3.11.12");
      console.log("");
    }
    if (missing.some((item) => item.includes("Node"))) {
      console.log(tr(ctx.langZh, "Node.js (example):", "Node.js（示例）："));
      if (ctx.platform.isWin) console.log("  nvm install 22.22.0 && nvm use 22.22.0");
      else console.log("  nvm install 22 && nvm use 22");
      console.log("");
    }
    process.exit(1);
  }

  return { pythonCmd: python.cmd };
}
