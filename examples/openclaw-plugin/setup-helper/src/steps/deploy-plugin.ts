import { existsSync } from "node:fs";
import { cp, mkdir, rm, rename, writeFile } from "node:fs/promises";
import { join, dirname, relative } from "node:path";
import type { InstallContext } from "../types.js";
import { downloadFile } from "../lib/download.js";
import { run } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logError, createSpinner } from "../ui/prompts.js";

async function downloadPluginFile(
  destDir: string,
  fileName: string,
  url: string,
  required: boolean,
  index: number,
  total: number,
  langZh: boolean,
): Promise<void> {
  const destPath = join(destDir, fileName);
  process.stdout.write(`  [${index}/${total}] ${fileName} `);

  const { ok, status, saw404 } = await downloadFile(url, destPath);

  if (ok) {
    console.log(" OK");
    return;
  }

  if (saw404 || status === 404) {
    if (fileName === ".gitignore") {
      await mkdir(dirname(destPath), { recursive: true });
      await writeFile(destPath, "node_modules/\n", "utf8");
      console.log(" OK");
      return;
    }
    console.log(tr(langZh, " skip", " 跳过"));
    return;
  }

  if (!required) {
    console.log("");
    logError(
      tr(
        langZh,
        `Optional file failed after retries (HTTP ${status || "network"}): ${url}`,
        `可选文件重试失败（HTTP ${status || "网络错误"}）: ${url}`,
      ),
    );
    process.exit(1);
  }

  console.log("");
  logError(tr(langZh, `Download failed after retries: ${url}`, `下载失败（已重试）: ${url}`));
  process.exit(1);
}

async function downloadPlugin(
  destDir: string,
  ctx: InstallContext,
): Promise<void> {
  const config = ctx.pluginConfig!;
  const ghRaw = `https://raw.githubusercontent.com/${ctx.repo}/${ctx.pluginVersion}`;
  const total = config.files.required.length + config.files.optional.length;

  await mkdir(destDir, { recursive: true });

  logInfo(
    tr(
      ctx.langZh,
      `Downloading plugin from ${ctx.repo}@${ctx.pluginVersion} (${total} files)...`,
      `正在从 ${ctx.repo}@${ctx.pluginVersion} 下载插件（共 ${total} 个文件）...`,
    ),
  );

  let i = 0;
  for (const name of config.files.required) {
    if (!name) continue;
    i++;
    const url = `${ghRaw}/examples/${config.dir}/${name}`;
    await downloadPluginFile(destDir, name, url, true, i, total, ctx.langZh);
  }
  for (const name of config.files.optional) {
    if (!name) continue;
    i++;
    const url = `${ghRaw}/examples/${config.dir}/${name}`;
    await downloadPluginFile(destDir, name, url, false, i, total, ctx.langZh);
  }

  logInfo(tr(ctx.langZh, "Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
  const npmArgs = config.npmOmitDev
    ? ["install", "--omit=dev", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry]
    : ["install", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry];
  await run("npm", npmArgs, { cwd: destDir, silent: false });
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
  const s = createSpinner();
  s.start(tr(ctx.langZh, "Deploying plugin files...", "正在部署插件文件..."));

  const stagingDir = await createPluginStagingDir(ctx);
  try {
    await downloadPlugin(stagingDir, ctx);
    await finalizePluginDeployment(stagingDir, ctx.pluginDest);
    s.stop(
      tr(ctx.langZh, `Plugin deployed: ${ctx.pluginDest}`, `插件部署完成: ${ctx.pluginDest}`),
    );
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true });
    s.stop(tr(ctx.langZh, "Plugin deployment failed", "插件部署失败"));
    throw error;
  }
}

export async function deployPluginFromLocal(
  ctx: InstallContext,
  localPluginDir: string,
): Promise<void> {
  const s = createSpinner();
  s.start(tr(ctx.langZh, "Deploying local plugin...", "正在部署本地插件..."));

  const stagingDir = await createPluginStagingDir(ctx);
  try {
    await deployLocalPlugin(localPluginDir, stagingDir);
    logInfo(tr(ctx.langZh, "Installing plugin npm dependencies...", "正在安装插件 npm 依赖..."));
    const config = ctx.pluginConfig!;
    const npmArgs = config.npmOmitDev
      ? ["install", "--omit=dev", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry]
      : ["install", "--no-audit", "--no-fund", "--registry", ctx.npmRegistry];
    await run("npm", npmArgs, { cwd: stagingDir, silent: false });
    await finalizePluginDeployment(stagingDir, ctx.pluginDest);
    s.stop(
      tr(ctx.langZh, `Plugin deployed: ${ctx.pluginDest}`, `插件部署完成: ${ctx.pluginDest}`),
    );
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true });
    s.stop(tr(ctx.langZh, "Plugin deployment failed", "插件部署失败"));
    throw error;
  }
}
