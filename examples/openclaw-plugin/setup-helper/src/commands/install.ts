import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import pc from "picocolors";
import type { InstallContext } from "../types.js";
import { PLUGIN_VARIANTS } from "../types.js";
import { getExistingEnvFiles, getInstallStatePathForPlugin, wrapCommand, getUpgradeAuditPath } from "../lib/config.js";
import { resolvePluginConfig } from "../lib/manifest.js";
import { deriveOpenvikingVersionFromPluginVersion } from "../lib/version.js";
import { tr } from "../ui/messages.js";
import { intro, outro, logInfo, logSuccess, logStep, logWarning, logError, selectOption, confirmPrompt } from "../ui/prompts.js";
import { checkOpenClaw } from "../steps/check-env.js";
import { validateEnvironment } from "../steps/check-env.js";
import {
  resolveDefaultPluginVersion,
  validateRequestedPluginVersion,
  syncOpenvikingVersionWithPluginVersion,
  checkOpenClawCompatibility,
  checkRequestedOpenVikingCompatibility,
  fetchAvailableVersions,
} from "../steps/resolve-version.js";
import { selectWorkdir, selectMode } from "../steps/select-mode.js";
import { collectRemoteConfig, configureOvConf } from "../steps/collect-config.js";
import { installOpenViking } from "../steps/install-runtime.js";
import { deployPluginFromRemote, deployPluginFromLocal } from "../steps/deploy-plugin.js";
import { configureOpenClawPlugin } from "../steps/configure-plugin.js";
import { writeOpenvikingEnv } from "../steps/write-env.js";
import { printCurrentVersionInfo } from "./version.js";
import { rollbackLastUpgradeOperation } from "./rollback.js";
import { prepareStrongPluginUpgrade, shouldClaimTargetSlot } from "./upgrade.js";
import {
  detectInstalledPluginState,
  formatInstalledStateLabel,
  detectInstalledOpenVikingVersion,
} from "../steps/detect-installed.js";

function getPluginVariantById(pluginId: string) {
  return PLUGIN_VARIANTS.find((v) => v.id === pluginId) || null;
}

async function writeInstallStateFile(
  ctx: InstallContext,
  opts: {
    operation: string;
    fromVersion: string;
    configBackupPath: string;
    pluginBackups: Array<{ pluginId: string; backupDir: string }>;
  },
): Promise<void> {
  const installStatePath = getInstallStatePathForPlugin(ctx, ctx.pluginConfig?.id || "openviking");
  const state = {
    pluginId: ctx.pluginConfig?.id || "openviking",
    generation:
      getPluginVariantById(ctx.pluginConfig?.id || "openviking")?.generation || "unknown",
    requestedRef: ctx.pluginVersion,
    releaseId: ctx.pluginConfig?.releaseId || "",
    operation: opts.operation,
    fromVersion: opts.fromVersion || "",
    configBackupPath: opts.configBackupPath || "",
    pluginBackups: opts.pluginBackups || [],
    installedAt: new Date().toISOString(),
    repo: ctx.repo,
  };
  await writeFile(installStatePath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

export async function runInstall(ctx: InstallContext): Promise<void> {
  intro(ctx);

  ctx = await selectWorkdir(ctx);

  if (ctx.showCurrentVersion) {
    await printCurrentVersionInfo(ctx);
    return;
  }

  if (ctx.rollbackLastUpgrade) {
    logInfo(tr(ctx.langZh, "Mode: rollback last plugin upgrade", "模式: 回滚最近一次插件升级"));
    if (ctx.pluginVersionExplicit) {
      logWarning("--plugin-version is ignored in --rollback mode.");
    }
    await rollbackLastUpgradeOperation(ctx);
    return;
  }

  ctx = await resolveDefaultPluginVersion(ctx);
  validateRequestedPluginVersion(ctx);

  logStep(
    [
      tr(ctx.langZh, "Installation target", "安装目标"),
      `  ${tr(ctx.langZh, "Target:", "目标实例:")} ${ctx.openclawDir}`,
      `  ${tr(ctx.langZh, "Repository:", "仓库:")} ${ctx.repo}`,
      `  ${tr(ctx.langZh, "Plugin version:", "插件版本:")} ${ctx.pluginVersion}`,
      ctx.openvikingVersion
        ? `  ${tr(ctx.langZh, "OpenViking version:", "OpenViking 版本:")} ${ctx.openvikingVersion}`
        : "",
    ]
      .filter(Boolean)
      .join("\n"),
  );

  let skipModeSelection = false;

  if (!ctx.upgradePluginOnly) {
    const existingState = await detectInstalledPluginState(ctx);

    if (existingState.generation !== "none") {
      const installedLabel = formatInstalledStateLabel(existingState);
      const detectedMode = existingState.detections.some((d) => {
        const entries = (existingState.config?.plugins as Record<string, unknown>)?.entries as
          | Record<string, { config?: Record<string, unknown> }>
          | undefined;
        const cfg = entries?.[d.variant.id]?.config;
        return cfg && (cfg.mode === "local" || cfg.configPath || cfg.port);
      })
        ? "local"
        : "remote";

      logWarning(
        tr(
          ctx.langZh,
          `Detected existing plugin: ${installedLabel}`,
          `检测到已安装的插件: ${installedLabel}`,
        ),
      );

      if (ctx.interactive) {
        type ReinstallAction = "upgrade-plugin" | "upgrade-all" | "fresh-install" | "cancel";
        const options: Array<{ value: ReinstallAction; label: string; hint?: string }> =
          detectedMode === "local"
            ? [
                {
                  value: "upgrade-plugin",
                  label: tr(
                    ctx.langZh,
                    "Upgrade plugin only (keep OpenViking service unchanged)",
                    "仅升级插件（保留 OpenViking 服务不变）",
                  ),
                },
                {
                  value: "upgrade-all",
                  label: tr(
                    ctx.langZh,
                    "Upgrade plugin + OpenViking service",
                    "升级插件 + OpenViking 服务",
                  ),
                  hint: tr(ctx.langZh, "recommended for major version updates", "大版本更新推荐"),
                },
                {
                  value: "fresh-install",
                  label: tr(
                    ctx.langZh,
                    "Fresh install (overwrite everything)",
                    "全新安装（覆盖所有内容）",
                  ),
                },
                {
                  value: "cancel",
                  label: tr(ctx.langZh, "Cancel", "取消"),
                },
              ]
            : [
                {
                  value: "upgrade-plugin",
                  label: tr(
                    ctx.langZh,
                    "Upgrade plugin only",
                    "仅升级插件",
                  ),
                },
                {
                  value: "fresh-install",
                  label: tr(
                    ctx.langZh,
                    "Fresh install (overwrite everything)",
                    "全新安装（覆盖所有内容）",
                  ),
                },
                {
                  value: "cancel",
                  label: tr(ctx.langZh, "Cancel", "取消"),
                },
              ];

        const action = await selectOption<ReinstallAction>(
          tr(
            ctx.langZh,
            "What would you like to do?",
            "请选择操作：",
          ),
          options,
          "upgrade-plugin",
        );

        if (action === "cancel") {
          logInfo(tr(ctx.langZh, "Installation cancelled.", "安装已取消。"));
          process.exit(0);
        }

        if (action === "upgrade-plugin" || action === "upgrade-all") {
          if (!ctx.pluginVersionExplicit) {
            const versions = await fetchAvailableVersions(ctx);
            if (versions.length > 0) {
              const versionOptions = versions.map((v, i) => ({
                value: v,
                label: v,
                hint: i === 0
                  ? tr(ctx.langZh, "latest", "最新")
                  : undefined,
              }));
              const selectedVersion = await selectOption(
                tr(
                  ctx.langZh,
                  "Select target version:",
                  "选择目标版本：",
                ),
                versionOptions,
                versions[0],
              );
              const ovVersion = deriveOpenvikingVersionFromPluginVersion(selectedVersion);
              ctx = {
                ...ctx,
                pluginVersion: selectedVersion,
                pluginVersionExplicit: true,
                openvikingVersion: ovVersion || ctx.openvikingVersion,
              };
            }
          }
        }

        if (action === "upgrade-plugin") {
          ctx = { ...ctx, upgradePluginOnly: true };
        } else if (action === "upgrade-all") {
          skipModeSelection = true;
          ctx = { ...ctx, upgradePluginOnly: false, mode: "local" };
        }
        // "fresh-install" falls through to normal install flow
      } else {
        // Non-interactive (-y): auto-upgrade based on detected mode
        logInfo(
          tr(
            ctx.langZh,
            `Non-interactive mode: auto-upgrading (detected mode: ${detectedMode})`,
            `非交互模式：自动升级（检测到模式: ${detectedMode}）`,
          ),
        );

        if (detectedMode === "local") {
          skipModeSelection = true;
          ctx = { ...ctx, mode: "local" };
        } else {
          ctx = { ...ctx, upgradePluginOnly: true };
        }
      }
    }
    // If generation === "none" (first install), fall through to normal flow
  }

  if (ctx.upgradePluginOnly) {
    ctx = { ...ctx, mode: "local" };
    logInfo("Mode: plugin upgrade only");
    await checkOpenClaw(ctx);
    const pluginConfig = await resolvePluginConfig(ctx.repo, ctx.pluginVersion);
    ctx = {
      ...ctx,
      pluginConfig,
      pluginDest: join(ctx.openclawDir, "extensions", pluginConfig.id),
    };
    logInfo(
      tr(
        ctx.langZh,
        `Plugin: ${pluginConfig.id} (${pluginConfig.kind})`,
        `插件: ${pluginConfig.id} (${pluginConfig.kind})`,
      ),
    );
    await checkOpenClawCompatibility(ctx);
    ctx = await prepareStrongPluginUpgrade(ctx);
  } else {
    if (!skipModeSelection && !ctx.modeExplicit) {
      ctx = await selectMode(ctx);
    }
    logInfo(tr(ctx.langZh, `Mode: ${ctx.mode}`, `模式: ${ctx.mode}`));

    if (ctx.mode === "local") {
      const { pythonCmd } = await validateEnvironment(ctx);
      ctx = { ...ctx, pythonPath: pythonCmd };
      await checkOpenClaw(ctx);
      const pluginConfig = await resolvePluginConfig(ctx.repo, ctx.pluginVersion);
      ctx = {
        ...ctx,
        pluginConfig,
        pluginDest: join(ctx.openclawDir, "extensions", pluginConfig.id),
      };
      logInfo(
        tr(
          ctx.langZh,
          `Plugin: ${pluginConfig.id} (${pluginConfig.kind})`,
          `插件: ${pluginConfig.id} (${pluginConfig.kind})`,
        ),
      );
      await checkOpenClawCompatibility(ctx);
      checkRequestedOpenVikingCompatibility(ctx);
      ctx = await installOpenViking(ctx);
      ctx = await configureOvConf(ctx);
    } else {
      await checkOpenClaw(ctx);
      const pluginConfig = await resolvePluginConfig(ctx.repo, ctx.pluginVersion);
      ctx = {
        ...ctx,
        pluginConfig,
        pluginDest: join(ctx.openclawDir, "extensions", pluginConfig.id),
      };
      logInfo(
        tr(
          ctx.langZh,
          `Plugin: ${pluginConfig.id} (${pluginConfig.kind})`,
          `插件: ${pluginConfig.id} (${pluginConfig.kind})`,
        ),
      );
      await checkOpenClawCompatibility(ctx);
      ctx = await collectRemoteConfig(ctx);
    }
  }

  const localPluginDir = ctx.openvikingRepo
    ? join(ctx.openvikingRepo, "examples", ctx.pluginConfig?.dir || "openclaw-plugin")
    : "";
  if (ctx.openvikingRepo && existsSync(join(localPluginDir, "index.ts"))) {
    logInfo(
      tr(
        ctx.langZh,
        `Using local plugin from repo: ${localPluginDir}`,
        `使用仓库内插件: ${localPluginDir}`,
      ),
    );
    await deployPluginFromLocal(ctx, localPluginDir);
  } else {
    await deployPluginFromRemote(ctx);
  }

  await configureOpenClawPlugin(
    ctx,
    ctx.upgradePluginOnly
      ? {
          runtimeConfig: ctx.upgradeRuntimeConfig,
          claimSlot: ctx.installedUpgradeState
            ? shouldClaimTargetSlot(ctx)
            : true,
        }
      : { preserveExistingConfig: false },
  );

  await writeInstallStateFile(ctx, {
    operation: ctx.upgradePluginOnly ? "upgrade" : "install",
    fromVersion: ctx.upgradeAudit?.fromVersion || "",
    configBackupPath: ctx.upgradeAudit?.configBackupPath || "",
    pluginBackups: ctx.upgradeAudit?.pluginBackups || [],
  });

  if (ctx.upgradeAudit) {
    ctx.upgradeAudit.completedAt = new Date().toISOString();
    const { writeUpgradeAuditFile } = await import("../lib/backup.js");
    await writeUpgradeAuditFile(ctx, ctx.upgradeAudit);
  }

  let envFiles = getExistingEnvFiles(ctx);
  if (!ctx.upgradePluginOnly) {
    envFiles = await writeOpenvikingEnv(ctx, { includePython: ctx.mode === "local" });
  } else if (!envFiles && ctx.openclawDir !== ctx.defaultOpenclawDir) {
    envFiles = await writeOpenvikingEnv(ctx, { includePython: false });
  }
  ctx = { ...ctx, envFiles };

  // Print completion summary
  console.log("");
  logSuccess(
    pc.bold(tr(ctx.langZh, "Installation complete!", "安装完成！")),
  );

  if (ctx.upgradeAudit) {
    logInfo(
      tr(
        ctx.langZh,
        `Upgrade path: ${ctx.upgradeAudit.fromVersion} -> ${ctx.upgradeAudit.toVersion}`,
        `升级路径: ${ctx.upgradeAudit.fromVersion} -> ${ctx.upgradeAudit.toVersion}`,
      ),
    );
    logInfo(
      tr(
        ctx.langZh,
        `Rollback audit file: ${getUpgradeAuditPath(ctx)}`,
        `回滚审计文件: ${getUpgradeAuditPath(ctx)}`,
      ),
    );
  }

  const startCmds = [
    `1) ${wrapCommand("openclaw --version", envFiles, ctx.platform.isWin)}`,
    `2) ${wrapCommand("openclaw onboard", envFiles, ctx.platform.isWin)}`,
    `3) ${wrapCommand("openclaw gateway", envFiles, ctx.platform.isWin)}`,
    `4) ${wrapCommand("openclaw status", envFiles, ctx.platform.isWin)}`,
  ];

  logStep(
    [
      ctx.mode === "local"
        ? tr(ctx.langZh, "Run these commands to start OpenClaw + OpenViking:", "请按以下命令启动 OpenClaw + OpenViking：")
        : tr(ctx.langZh, "Run these commands to start OpenClaw:", "请按以下命令启动 OpenClaw："),
      ...startCmds.map((c) => `  ${c}`),
    ].join("\n"),
  );

  if (ctx.mode === "local") {
    if (ctx.ovConfNeedsManualEdit) {
      logError(
        pc.bold(
          tr(
            ctx.langZh,
            "IMPORTANT: You must edit ov.conf before starting OpenViking!",
            "重要：启动 OpenViking 前必须先修改 ov.conf！",
          ),
        ),
      );
      logWarning(
        tr(
          ctx.langZh,
          `The config file was generated with default values. At minimum, set your VLM API Key and Embedding API Key:\n  ${join(ctx.openvikingDir, "ov.conf")}`,
          `配置文件已使用默认值生成。请至少填写 VLM API Key 和 Embedding API Key：\n  ${join(ctx.openvikingDir, "ov.conf")}`,
        ),
      );
    } else {
      logInfo(
        tr(
          ctx.langZh,
          `You can edit the config freely: ${ctx.openvikingDir}/ov.conf`,
          `你可以按需自由修改配置文件: ${ctx.openvikingDir}/ov.conf`,
        ),
      );
    }
  } else {
    logInfo(tr(ctx.langZh, `Remote server: ${ctx.remoteBaseUrl}`, `远程服务器: ${ctx.remoteBaseUrl}`));
  }

  outro(tr(ctx.langZh, "Done!", "完成！"));
}
