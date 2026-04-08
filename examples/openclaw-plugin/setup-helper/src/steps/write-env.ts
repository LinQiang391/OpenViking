import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { EnvFiles, InstallContext } from "../types.js";
import { runCapture, resolveAbsoluteCommand } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logWarning } from "../ui/prompts.js";

async function discoverOpenvikingPython(failedPy: string, isWin: boolean): Promise<string> {
  const candidates = isWin
    ? ["python3", "python", "py -3"]
    : ["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"];
  for (const candidate of candidates) {
    if (candidate === failedPy) continue;
    const resolved = await resolveAbsoluteCommand(candidate);
    if (!resolved || resolved === candidate || resolved === failedPy) continue;
    const check = await runCapture(resolved, ["-c", "import openviking"], { shell: false });
    if (check.code === 0) return resolved;
  }
  return "";
}

async function resolvePythonPath(ctx: InstallContext): Promise<string> {
  if (ctx.pythonPath) return ctx.pythonPath;
  const { checkPython } = await import("./check-env.js");
  const python = await checkPython(ctx);
  return python.cmd || "";
}

export async function writeOpenvikingEnv(
  ctx: InstallContext,
  opts: { includePython: boolean },
): Promise<EnvFiles | null> {
  const needStateDir = ctx.openclawDir !== ctx.defaultOpenclawDir;
  let pythonPath = "";

  if (opts.includePython) {
    pythonPath = await resolvePythonPath(ctx);
    if (!pythonPath) {
      pythonPath =
        (process.env.OPENVIKING_PYTHON || "").trim() || (ctx.platform.isWin ? "python" : "python3");
      logWarning(
        tr(
          ctx.langZh,
          "Could not resolve absolute Python path; wrote fallback OPENVIKING_PYTHON to openviking.env.",
          "未能解析 Python 绝对路径，已在 openviking.env 中写入后备值。",
        ),
      );
    }

    if (pythonPath) {
      const verify = await runCapture(pythonPath, ["-c", "import openviking"], { shell: false });
      if (verify.code !== 0) {
        logWarning(
          tr(
            ctx.langZh,
            `Resolved Python (${pythonPath}) cannot import openviking.`,
            `解析到的 Python（${pythonPath}）无法 import openviking。`,
          ),
        );
        const corrected = await discoverOpenvikingPython(pythonPath, ctx.platform.isWin);
        if (corrected) {
          logInfo(
            tr(
              ctx.langZh,
              `Auto-corrected OPENVIKING_PYTHON to ${corrected}`,
              `已自动修正 OPENVIKING_PYTHON 为 ${corrected}`,
            ),
          );
          pythonPath = corrected;
        } else {
          logWarning(
            tr(
              ctx.langZh,
              "Could not auto-detect the correct Python. Edit OPENVIKING_PYTHON in the env file manually.",
              "无法自动检测正确的 Python。请手动修改 env 文件中的 OPENVIKING_PYTHON。",
            ),
          );
        }
      }
    }
  }

  if (!needStateDir && !pythonPath) return null;

  await mkdir(ctx.openclawDir, { recursive: true });

  if (ctx.platform.isWin) {
    const batLines = ["@echo off"];
    const psLines: string[] = [];

    if (needStateDir) {
      batLines.push(`set "OPENCLAW_STATE_DIR=${ctx.openclawDir.replace(/"/g, '""')}"`);
      psLines.push(
        `$env:OPENCLAW_STATE_DIR = "${ctx.openclawDir.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`,
      );
    }
    if (pythonPath) {
      batLines.push(`set "OPENVIKING_PYTHON=${pythonPath.replace(/"/g, '""')}"`);
      psLines.push(
        `$env:OPENVIKING_PYTHON = "${pythonPath.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`,
      );
    }

    const batPath = join(ctx.openclawDir, "openviking.env.bat");
    const ps1Path = join(ctx.openclawDir, "openviking.env.ps1");
    await writeFile(batPath, `${batLines.join("\r\n")}\r\n`, "utf8");
    await writeFile(ps1Path, `${psLines.join("\n")}\n`, "utf8");

    logInfo(
      tr(ctx.langZh, `Environment file generated: ${batPath}`, `已生成环境文件: ${batPath}`),
    );
    return { shellPath: batPath, powershellPath: ps1Path };
  }

  const lines: string[] = [];
  if (needStateDir) {
    lines.push(`export OPENCLAW_STATE_DIR='${ctx.openclawDir.replace(/'/g, "'\"'\"'")}'`);
  }
  if (pythonPath) {
    lines.push(`export OPENVIKING_PYTHON='${pythonPath.replace(/'/g, "'\"'\"'")}'`);
  }

  const envPath = join(ctx.openclawDir, "openviking.env");
  await writeFile(envPath, `${lines.join("\n")}\n`, "utf8");
  logInfo(
    tr(ctx.langZh, `Environment file generated: ${envPath}`, `已生成环境文件: ${envPath}`),
  );
  return { shellPath: envPath };
}
