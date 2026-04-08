import { join } from "node:path";
import type { InstallContext, PluginBackup } from "../types.js";
import { PLUGIN_VARIANTS } from "../types.js";
import { backupOpenClawConfig, backupPluginDirectory, writeUpgradeAuditFile } from "../lib/backup.js";
import { getOpenClawEnv } from "../lib/config.js";
import { runCapture } from "../lib/process.js";
import { versionGte, isSemverLike } from "../lib/version.js";
import { tr } from "../ui/messages.js";
import { logInfo, logWarning, logError, logSuccess, confirmPrompt } from "../ui/prompts.js";
import {
  detectInstalledPluginState,
  formatInstalledStateLabel,
  formatTargetVersionLabel,
  prepareUpgradeRuntimeConfig,
} from "../steps/detect-installed.js";
import { cleanupInstalledPluginConfig } from "../steps/configure-plugin.js";

function shouldClaimTargetSlot(ctx: InstallContext): boolean {
  const installedState = ctx.installedUpgradeState;
  if (!installedState) return true;

  const currentOwner = (
    (installedState.config as Record<string, Record<string, Record<string, string>>>)?.plugins
      ?.slots ?? {}
  )[ctx.pluginConfig!.slot];

  if (
    !currentOwner ||
    currentOwner === "none" ||
    currentOwner === "legacy" ||
    currentOwner === ctx.pluginConfig!.id
  ) {
    return true;
  }

  const currentOwnerVariant = PLUGIN_VARIANTS.find((v) => v.id === currentOwner);
  if (
    currentOwnerVariant &&
    installedState.detections.some((item) => item.variant.id === currentOwnerVariant.id)
  ) {
    return true;
  }
  return false;
}

async function stopOpenClawGatewayForUpgrade(ctx: InstallContext): Promise<void> {
  const result = await runCapture("openclaw", ["gateway", "stop"], {
    env: getOpenClawEnv(ctx),
    shell: ctx.platform.isWin,
  });
  if (result.code === 0) {
    logInfo(
      tr(
        ctx.langZh,
        "Stopped OpenClaw gateway before plugin upgrade",
        "升级插件前已停止 OpenClaw gateway",
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

function extractInstalledSemver(installedState: ReturnType<typeof formatInstalledStateLabel> extends infer T ? T : string): string {
  const match = String(installedState).match(/[@#](v?\d+(?:\.\d+){1,2})/);
  return match?.[1] || "";
}

export async function prepareStrongPluginUpgrade(ctx: InstallContext): Promise<InstallContext> {
  const installedState = await detectInstalledPluginState(ctx);
  if (installedState.generation === "none") {
    logError(
      tr(
        ctx.langZh,
        "Plugin upgrade mode requires an existing OpenViking plugin entry in openclaw.json.",
        "插件升级模式要求 openclaw.json 中已经存在 OpenViking 插件记录。",
      ),
    );
    process.exit(1);
  }

  const upgradeRuntimeConfig = await prepareUpgradeRuntimeConfig(ctx, installedState);
  const fromVersion = formatInstalledStateLabel(installedState);
  const toVersion = formatTargetVersionLabel(ctx);

  // Version comparison: warn if not upgrading to a newer version
  const installedSemver = extractInstalledSemver(fromVersion);
  const targetSemver = ctx.pluginVersion;
  if (installedSemver && isSemverLike(installedSemver) && isSemverLike(targetSemver)) {
    if (versionGte(installedSemver, targetSemver) && versionGte(targetSemver, installedSemver)) {
      logSuccess(
        tr(
          ctx.langZh,
          `Plugin is already at version ${installedSemver}. No upgrade needed.`,
          `插件已经是 ${installedSemver} 版本，无需升级。`,
        ),
      );
      process.exit(0);
    }
    if (versionGte(installedSemver, targetSemver)) {
      logWarning(
        tr(
          ctx.langZh,
          `Target version ${targetSemver} is older than installed ${installedSemver}. This is a downgrade.`,
          `目标版本 ${targetSemver} 低于已安装的 ${installedSemver}，这是一次降级操作。`,
        ),
      );
      if (ctx.interactive) {
        const proceed = await confirmPrompt(
          tr(ctx.langZh, "Continue with downgrade?", "确认继续降级？"),
          false,
        );
        if (!proceed) {
          logInfo(tr(ctx.langZh, "Upgrade cancelled.", "升级已取消。"));
          process.exit(0);
        }
      }
    }
  }

  let updatedCtx: InstallContext = {
    ...ctx,
    installedUpgradeState: installedState,
    upgradeRuntimeConfig,
    mode: upgradeRuntimeConfig.mode,
  };

  if (upgradeRuntimeConfig.mode === "remote") {
    updatedCtx = {
      ...updatedCtx,
      remoteBaseUrl: upgradeRuntimeConfig.baseUrl || ctx.remoteBaseUrl,
      remoteApiKey: upgradeRuntimeConfig.apiKey || "",
      remoteAgentId: upgradeRuntimeConfig.agentId || "",
    };
  } else {
    updatedCtx = {
      ...updatedCtx,
      selectedServerPort: upgradeRuntimeConfig.port || ctx.selectedServerPort,
    };
  }

  logInfo(
    tr(
      ctx.langZh,
      `Detected installed OpenViking plugin state: ${installedState.generation}`,
      `检测到已安装 OpenViking 插件状态: ${installedState.generation}`,
    ),
  );
  logInfo(tr(ctx.langZh, `Upgrade path: ${fromVersion} -> ${toVersion}`, `升级路径: ${fromVersion} -> ${toVersion}`));

  await stopOpenClawGatewayForUpgrade(updatedCtx);
  const configBackupPath = await backupOpenClawConfig(updatedCtx, installedState.configPath);
  logInfo(
    tr(
      ctx.langZh,
      `Backed up openclaw.json: ${configBackupPath}`,
      `已备份 openclaw.json: ${configBackupPath}`,
    ),
  );

  const pluginBackups: PluginBackup[] = [];
  for (const detection of installedState.detections) {
    const backup = await backupPluginDirectory(updatedCtx, detection.variant);
    if (backup) {
      pluginBackups.push(backup);
      logInfo(
        tr(
          ctx.langZh,
          `Backed up plugin directory: ${backup.backupDir}`,
          `已备份插件目录: ${backup.backupDir}`,
        ),
      );
    }
  }

  const upgradeAudit = {
    operation: "upgrade",
    createdAt: new Date().toISOString(),
    fromVersion,
    toVersion,
    configBackupPath,
    pluginBackups,
    runtimeMode: updatedCtx.mode,
  };
  await writeUpgradeAuditFile(updatedCtx, upgradeAudit);
  updatedCtx = { ...updatedCtx, upgradeAudit };

  await cleanupInstalledPluginConfig(updatedCtx);

  logInfo(
    tr(
      ctx.langZh,
      "Upgrade will keep the existing OpenViking runtime and re-apply only the minimum plugin runtime settings.",
      "升级将保留现有 OpenViking 运行时，并只回填最小插件运行配置。",
    ),
  );

  return updatedCtx;
}

export { shouldClaimTargetSlot };
