import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import type { InstallContext, PipInstallResult } from "../types.js";
import { DEFAULT_PIP_INDEX_URL, OFFICIAL_PIP_INDEX_URL } from "../types.js";
import { run, runCapture, runLiveCapture, resolveAbsoluteCommand } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logWarning, logError, createSpinner } from "../ui/prompts.js";

function shouldFallbackToOfficialPypi(output: string): boolean {
  if (process.env.PIP_INDEX_URL?.trim()) return false;
  return /Could not find a version that satisfies the requirement|No matching distribution found|HTTP error 404|too many 502 error responses|Temporary failure in name resolution|Failed to establish a new connection|Connection (?:timed out|reset by peer)|Read timed out|ProxyError|SSLError|TLSV1_ALERT|Remote end closed connection/i.test(
    output,
  );
}

async function runPipInstallWithFallback(
  py: string,
  pipArgs: string[],
  pipIndexUrl: string,
  opts: { shell?: boolean } = {},
): Promise<PipInstallResult> {
  const shell = opts.shell ?? false;
  const primaryResult = await runLiveCapture(py, [...pipArgs, "-i", pipIndexUrl], { shell });
  if (primaryResult.code === 0) {
    return { result: primaryResult, usedFallback: false };
  }

  const primaryOutput = `${primaryResult.out}\n${primaryResult.err}`;
  if (!shouldFallbackToOfficialPypi(primaryOutput)) {
    return { result: primaryResult, usedFallback: false };
  }

  logWarning(`Install from mirror failed. Retrying with official PyPI: ${OFFICIAL_PIP_INDEX_URL}`);
  const fallbackResult = await runLiveCapture(py, [...pipArgs, "-i", OFFICIAL_PIP_INDEX_URL], {
    shell,
  });
  return { result: fallbackResult, usedFallback: true, primaryResult, fallbackResult };
}

export async function installOpenViking(ctx: InstallContext): Promise<InstallContext> {
  if (process.env.SKIP_OPENVIKING === "1") {
    logInfo(
      tr(
        ctx.langZh,
        "Skipping OpenViking install (SKIP_OPENVIKING=1)",
        "跳过 OpenViking 安装 (SKIP_OPENVIKING=1)",
      ),
    );
    return ctx;
  }

  const { checkPython } = await import("./check-env.js");
  const python = await checkPython(ctx);
  if (!python.cmd) {
    logError(tr(ctx.langZh, "Python check failed.", "Python 校验失败"));
    process.exit(1);
  }

  const py = python.cmd;
  const pipIndexUrl = ctx.pipIndexUrl;

  if (ctx.openvikingRepo && existsSync(join(ctx.openvikingRepo, "pyproject.toml"))) {
    const s = createSpinner();
    s.start(
      tr(
        ctx.langZh,
        `Installing OpenViking from source: ${ctx.openvikingRepo}`,
        `正在从源码安装 OpenViking: ${ctx.openvikingRepo}`,
      ),
    );
    await run(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", pipIndexUrl], {
      silent: true,
    });
    await run(py, ["-m", "pip", "install", "-e", ctx.openvikingRepo]);
    s.stop(tr(ctx.langZh, "OpenViking installed ✓ (source)", "OpenViking 安装完成 ✓（源码）"));
    return { ...ctx, pythonPath: py };
  }

  const pkgSpec = ctx.openvikingVersion
    ? `openviking==${ctx.openvikingVersion}`
    : "openviking";

  const s = createSpinner();
  s.start(
    tr(
      ctx.langZh,
      `Installing OpenViking ${ctx.openvikingVersion || "(latest)"} from PyPI...`,
      `正在安装 OpenViking ${ctx.openvikingVersion || "（最新版）"} (PyPI)...`,
    ),
  );

  await runCapture(py, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", pipIndexUrl], {
    shell: false,
  });
  const { result: installResult } = await runPipInstallWithFallback(
    py,
    ["-m", "pip", "install", "--upgrade", "--progress-bar", "on", pkgSpec],
    pipIndexUrl,
    { shell: false },
  );
  if (installResult.code === 0) {
    s.stop(tr(ctx.langZh, "OpenViking installed ✓", "OpenViking 安装完成 ✓"));
    return { ...ctx, pythonPath: py };
  }

  const installOutput = `${installResult.out}\n${installResult.err}`;
  const shouldTryVenv =
    !ctx.platform.isWin &&
    /externally-managed-environment|externally managed|No module named pip/i.test(installOutput);

  if (shouldTryVenv) {
    const venvDir = join(ctx.openvikingDir, "venv");
    const venvPy = ctx.platform.isWin
      ? join(venvDir, "Scripts", "python.exe")
      : join(venvDir, "bin", "python");

    if (existsSync(venvPy)) {
      const reuseCheck = await runCapture(venvPy, ["-c", "import openviking"], { shell: false });
      if (reuseCheck.code === 0) {
        const { result: venvReuseInstall } = await runPipInstallWithFallback(
          venvPy,
          ["-m", "pip", "install", "--progress-bar", "on", "-U", pkgSpec],
          pipIndexUrl,
          { shell: false },
        );
        if (venvReuseInstall.code !== 0) {
          s.stop(
            tr(
              ctx.langZh,
              "OpenViking install failed in venv.",
              "在虚拟环境中安装 OpenViking 失败。",
            ),
          );
          console.log(venvReuseInstall.err || venvReuseInstall.out);
          process.exit(1);
        }
        s.stop(
          tr(ctx.langZh, "OpenViking installed ✓ (venv)", "OpenViking 安装完成 ✓（虚拟环境）"),
        );
        return { ...ctx, pythonPath: venvPy };
      }
    }

    await mkdir(ctx.openvikingDir, { recursive: true });
    const venvCreate = await runCapture(py, ["-m", "venv", venvDir], { shell: false });
    if (venvCreate.code !== 0) {
      s.stop(
        tr(
          ctx.langZh,
          "Cannot create Python virtual environment.",
          "无法创建 Python 虚拟环境。",
        ),
      );
      logError(
        tr(
          ctx.langZh,
          "python3-venv is not installed. Fix with:",
          "python3-venv 未安装，请执行以下命令修复：",
        ),
      );
      console.log(`
  apt update
  apt install -y software-properties-common
  add-apt-repository universe
  apt update
  apt install -y python3-venv
`);
      console.log(
        tr(
          ctx.langZh,
          "  Or force install into system Python (not recommended):",
          "  或强制安装到系统 Python（不推荐）：",
        ),
      );
      console.log(`  OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES=1 ov-install\n`);
      process.exit(1);
    }

    await runCapture(venvPy, ["-m", "pip", "install", "--upgrade", "pip", "-q", "-i", pipIndexUrl], {
      shell: false,
    });
    const { result: venvInstall } = await runPipInstallWithFallback(
      venvPy,
      ["-m", "pip", "install", "--upgrade", "--progress-bar", "on", pkgSpec],
      pipIndexUrl,
      { shell: false },
    );
    if (venvInstall.code === 0) {
      s.stop(
        tr(ctx.langZh, "OpenViking installed ✓ (venv)", "OpenViking 安装完成 ✓（虚拟环境）"),
      );
      return { ...ctx, pythonPath: venvPy };
    }

    s.stop(
      tr(
        ctx.langZh,
        "OpenViking install failed in venv.",
        "在虚拟环境中安装 OpenViking 失败。",
      ),
    );
    console.log(venvInstall.err || venvInstall.out);
    process.exit(1);
  }

  if (process.env.OPENVIKING_ALLOW_BREAK_SYSTEM_PACKAGES === "1") {
    const { result: systemInstall } = await runPipInstallWithFallback(
      py,
      ["-m", "pip", "install", "--upgrade", "--progress-bar", "on", "--break-system-packages", pkgSpec],
      pipIndexUrl,
      { shell: false },
    );
    if (systemInstall.code === 0) {
      s.stop(
        tr(ctx.langZh, "OpenViking installed ✓ (system)", "OpenViking 安装完成 ✓（系统）"),
      );
      return { ...ctx, pythonPath: py };
    }
  }

  s.stop(
    tr(
      ctx.langZh,
      "OpenViking install failed. Check Python >= 3.10 and pip.",
      "OpenViking 安装失败，请检查 Python >= 3.10 及 pip",
    ),
  );
  console.log(installResult.err || installResult.out);
  process.exit(1);
}
