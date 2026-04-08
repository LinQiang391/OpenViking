import { existsSync } from "node:fs";
import { cp, mkdir, rm, rename, writeFile } from "node:fs/promises";
import { join, dirname, relative } from "node:path";
import pc from "picocolors";
import type { InstallContext } from "../types.js";
import { downloadFile } from "../lib/download.js";
import { run } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logError, logStep, createSpinner } from "../ui/prompts.js";

async function downloadPluginFile(
  destDir: string,
  fileName: string,
  url: string,
  required: boolean,
): Promise<"ok" | "skip" | "fail"> {
  const destPath = join(destDir, fileName);
  const { ok, status, saw404 } = await downloadFile(url, destPath);

  if (ok) return "ok";

  if (saw404 || status === 404) {
    if (fileName === ".gitignore") {
      await mkdir(dirname(destPath), { recursive: true });
      await writeFile(destPath, "node_modules/\n", "utf8");
      return "ok";
    }
    return "skip";
  }

  if (!required) return "skip";
  return "fail";
}

async function downloadPlugin(
  destDir: string,
  ctx: InstallContext,
): Promise<void> {
  const config = ctx.pluginConfig!;
  const ghRaw = `https://raw.githubusercontent.com/${ctx.repo}/${ctx.pluginVersion}`;
  const allFiles = [
    ...config.files.required.map((f) => ({ name: f, required: true })),
    ...config.files.optional.map((f) => ({ name: f, required: false })),
  ].filter((f) => f.name);
  const total = allFiles.length;

  await mkdir(destDir, { recursive: true });

  const results: string[] = [];
  let failed = false;

  for (let i = 0; i < allFiles.length; i++) {
    const { name, required } = allFiles[i];
    const url = `${ghRaw}/examples/${config.dir}/${name}`;
    const result = await downloadPluginFile(destDir, name, url, required);

    if (result === "ok") {
      results.push(`  ${pc.green("✓")} ${name}`);
    } else if (result === "skip") {
      results.push(`  ${pc.dim("–")} ${name} ${pc.dim("(skipped)")}`);
    } else {
      results.push(`  ${pc.red("✗")} ${name} ${pc.red("FAILED")}`);
      failed = true;
    }
  }

  logStep(
    [
      tr(
        ctx.langZh,
        `Plugin files (${ctx.repo}@${ctx.pluginVersion}, ${total} files)`,
        `插件文件 (${ctx.repo}@${ctx.pluginVersion}，共 ${total} 个)`,
      ),
      ...results,
    ].join("\n"),
  );

  if (failed) {
    logError(
      tr(ctx.langZh, "Some required files failed to download.", "部分必需文件下载失败。"),
    );
    process.exit(1);
  }

  const s = createSpinner();
  s.start(tr(ctx.langZh, "Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
  const npmArgs = config.npmOmitDev
    ? ["install", "--omit=dev", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry]
    : ["install", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry];
  await run("npm", npmArgs, { cwd: destDir, silent: true });
  s.stop(tr(ctx.langZh, "npm dependencies installed ✓", "npm 依赖安装完成 ✓"));
}

async function deployLocalPlugin(localPluginDir: string, destDir: string): Promise<void> {
  await rm(destDir, { recursive: true, force: true });
  await mkdir(destDir, { recursive: true });
  await cp(localPluginDir, destDir, {
    recursive: true,
    force: true,
    filter: (sourcePath) => {
      const rel = relative(localPluginDir, sourcePath);
      if (!rel) return true;
      const firstSegment = rel.split(/[\\/]/)[0];
      return firstSegment !== "node_modules" && firstSegment !== ".git";
    },
  });
}

async function createPluginStagingDir(ctx: InstallContext): Promise<string> {
  const pluginId = ctx.pluginConfig?.id || "openviking";
  const extensionsDir = join(ctx.openclawDir, "extensions");
  const stagingDir = join(extensionsDir, `.${pluginId}.staging-${process.pid}-${Date.now()}`);
  await mkdir(extensionsDir, { recursive: true });
  await rm(stagingDir, { recursive: true, force: true });
  await mkdir(stagingDir, { recursive: true });
  return stagingDir;
}

async function finalizePluginDeployment(stagingDir: string, pluginDest: string): Promise<void> {
  await rm(pluginDest, { recursive: true, force: true });
  try {
    await rename(stagingDir, pluginDest);
  } catch {
    await cp(stagingDir, pluginDest, { recursive: true, force: true });
    await rm(stagingDir, { recursive: true, force: true });
  }
}

export async function deployPluginFromRemote(ctx: InstallContext): Promise<void> {
  const stagingDir = await createPluginStagingDir(ctx);
  try {
    await downloadPlugin(stagingDir, ctx);
    await finalizePluginDeployment(stagingDir, ctx.pluginDest);
    logInfo(
      tr(ctx.langZh, `Plugin deployed: ${ctx.pluginDest}`, `插件部署完成: ${ctx.pluginDest}`),
    );
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true });
    throw error;
  }
}

export async function deployPluginFromLocal(
  ctx: InstallContext,
  localPluginDir: string,
): Promise<void> {
  const stagingDir = await createPluginStagingDir(ctx);
  try {
    await deployLocalPlugin(localPluginDir, stagingDir);
    const s = createSpinner();
    s.start(tr(ctx.langZh, "Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
    const config = ctx.pluginConfig!;
    const npmArgs = config.npmOmitDev
      ? ["install", "--omit=dev", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry]
      : ["install", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry];
    await run("npm", npmArgs, { cwd: stagingDir, silent: true });
    s.stop(tr(ctx.langZh, "npm dependencies installed ✓", "npm 依赖安装完成 ✓"));
    await finalizePluginDeployment(stagingDir, ctx.pluginDest);
    logInfo(
      tr(ctx.langZh, `Plugin deployed: ${ctx.pluginDest}`, `插件部署完成: ${ctx.pluginDest}`),
    );
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true });
    throw error;
  }
}
