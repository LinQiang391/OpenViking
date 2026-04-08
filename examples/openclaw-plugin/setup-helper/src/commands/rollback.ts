import { existsSync } from "node:fs";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { InstallContext, UpgradeAudit } from "../types.js";
import { PLUGIN_VARIANTS } from "../types.js";
import {
  readJsonFileIfExists,
  getOpenClawConfigPath,
  getOpenClawConfigBackupPath,
  getUpgradeAuditPath,
} from "../lib/config.js";
import { moveDirWithFallback, writeUpgradeAuditFile } from "../lib/backup.js";
import { runCapture } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logError, logWarning, logSuccess, createSpinner } from "../ui/prompts.js";

async function stopOpenClawGateway(ctx: InstallContext): Promise<void> {
  const { getOpenClawEnv } = await import("../lib/config.js");
  const result = await runCapture("openclaw", ["gateway", "stop"], {
    env: getOpenClawEnv(ctx),
    shell: ctx.platform.isWin,
  });
  if (result.code === 0) {
    logInfo(
      tr(
        ctx.langZh,
        "Stopped OpenClaw gateway before rollback",
        "回滚前已停止 OpenClaw gateway",
      ),
    );
  } else {
    logWarning(
      tr(
        ctx.langZh,
        "OpenClaw gateway may not be running; continuing",
        "OpenClaw gateway 可能未在运行，继续执行",
      ),
    );
  }
}

export async function rollbackLastUpgradeOperation(ctx: InstallContext): Promise<void> {
  const auditPath = getUpgradeAuditPath(ctx);
  const audit = (await readJsonFileIfExists(auditPath)) as UpgradeAudit | null;
  if (!audit) {
    logError(
      tr(ctx.langZh, `No rollback audit file found at ${auditPath}.`, `未找到回滚审计文件: ${auditPath}`),
    );
    process.exit(1);
  }

  if (audit.rolledBackAt) {
    logWarning(
      tr(
        ctx.langZh,
        `The last recorded upgrade was already rolled back at ${audit.rolledBackAt}.`,
        `最近一次升级已在 ${audit.rolledBackAt} 回滚。`,
      ),
    );
  }

  const configBackupPath = audit.configBackupPath || getOpenClawConfigBackupPath(ctx);
  if (!existsSync(configBackupPath)) {
    logError(
      tr(
        ctx.langZh,
        `Rollback config backup is missing: ${configBackupPath}`,
        `回滚配置备份缺失: ${configBackupPath}`,
      ),
    );
    process.exit(1);
  }

  const pluginBackups = Array.isArray(audit.pluginBackups) ? audit.pluginBackups : [];
  if (pluginBackups.length === 0) {
    logError(
      tr(
        ctx.langZh,
        "Rollback audit file contains no plugin backups.",
        "回滚审计文件中没有插件备份信息。",
      ),
    );
    process.exit(1);
  }
  for (const pluginBackup of pluginBackups) {
    if (!pluginBackup?.pluginId || !pluginBackup?.backupDir || !existsSync(pluginBackup.backupDir)) {
      logError(
        tr(
          ctx.langZh,
          `Rollback plugin backup is missing: ${pluginBackup?.backupDir || "<unknown>"}`,
          `回滚插件备份缺失: ${pluginBackup?.backupDir || "<unknown>"}`,
        ),
      );
      process.exit(1);
    }
  }

  const s = createSpinner();
  s.start(
    tr(
      ctx.langZh,
      `Rolling back: ${audit.fromVersion || "unknown"} <- ${audit.toVersion || "unknown"}`,
      `开始回滚: ${audit.fromVersion || "unknown"} <- ${audit.toVersion || "unknown"}`,
    ),
  );

  await stopOpenClawGateway(ctx);

  const configText = await readFile(configBackupPath, "utf8");
  await writeFile(getOpenClawConfigPath(ctx), configText, "utf8");

  const extensionsDir = join(ctx.openclawDir, "extensions");
  await mkdir(extensionsDir, { recursive: true });
  for (const variant of PLUGIN_VARIANTS) {
    const liveDir = join(extensionsDir, variant.id);
    if (existsSync(liveDir)) {
      await rm(liveDir, { recursive: true, force: true });
    }
  }

  for (const pluginBackup of pluginBackups) {
    if (!pluginBackup?.pluginId || !pluginBackup?.backupDir) continue;
    if (!existsSync(pluginBackup.backupDir)) {
      logError(
        tr(
          ctx.langZh,
          `Rollback plugin backup is missing: ${pluginBackup.backupDir}`,
          `回滚插件备份缺失: ${pluginBackup.backupDir}`,
        ),
      );
      process.exit(1);
    }
    const destDir = join(extensionsDir, pluginBackup.pluginId);
    await moveDirWithFallback(pluginBackup.backupDir, destDir);
  }

  audit.rolledBackAt = new Date().toISOString();
  audit.rollbackConfigPath = configBackupPath;
  await writeUpgradeAuditFile(ctx, audit);

  s.stop(tr(ctx.langZh, "Rollback complete!", "回滚完成！"));
  logInfo(
    tr(
      ctx.langZh,
      "Run `openclaw gateway` and `openclaw status` to verify the restored plugin state.",
      "请运行 `openclaw gateway` 和 `openclaw status` 验证恢复后的插件状态。",
    ),
  );
}
