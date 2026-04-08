import type { InstallContext } from "../types.js";
import { runCapture } from "../lib/process.js";
import {
  pickLatestPluginTag,
  parseGitLsRemoteTags,
  isSemverLike,
  versionGte,
  deriveOpenvikingVersionFromPluginVersion,
} from "../lib/version.js";
import { tr } from "../ui/messages.js";
import { logInfo, logError, logWarning } from "../ui/prompts.js";

export function syncOpenvikingVersionWithPluginVersion(
  ctx: InstallContext,
  reason = "",
): InstallContext {
  if (ctx.openvikingVersion) return ctx;

  const derived = deriveOpenvikingVersionFromPluginVersion(ctx.pluginVersion);
  if (!derived) return ctx;

  logInfo(
    tr(
      ctx.langZh,
      `No OpenViking version specified; syncing runtime version to plugin version ${ctx.pluginVersion}${reason ? ` (${reason})` : ""}.`,
      `未指定 OpenViking 版本；已将运行时版本同步为插件版本 ${ctx.pluginVersion}${reason ? `（${reason}）` : ""}。`,
    ),
  );
  return { ...ctx, openvikingVersion: derived };
}

export async function resolveDefaultPluginVersion(ctx: InstallContext): Promise<InstallContext> {
  if (ctx.pluginVersion) return ctx;

  logInfo(
    tr(
      ctx.langZh,
      `No plugin version specified; resolving latest tag from ${ctx.repo}...`,
      `未指定插件版本，正在解析 ${ctx.repo} 的最新 tag...`,
    ),
  );

  const failures: string[] = [];
  const apiUrl = `https://api.github.com/repos/${ctx.repo}/tags?per_page=100`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(apiUrl, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "openviking-setup-helper",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (response.ok) {
      const payload = await response.json().catch(() => null);
      if (Array.isArray(payload)) {
        const latestTag = pickLatestPluginTag(
          payload.map((item: { name?: string }) => item?.name || ""),
        );
        if (latestTag) {
          let updated = { ...ctx, pluginVersion: latestTag, pluginVersionExplicit: false };
          updated = syncOpenvikingVersionWithPluginVersion(updated, "latest plugin tag");
          logInfo(
            tr(
              ctx.langZh,
              `Resolved default plugin version to latest tag: ${latestTag}`,
              `已将默认插件版本解析为最新 tag: ${latestTag}`,
            ),
          );
          return updated;
        }
      } else {
        failures.push("GitHub tags API returned an unexpected payload");
      }
    } else {
      failures.push(`GitHub tags API returned HTTP ${response.status}`);
    }
  } catch (error) {
    failures.push(`GitHub tags API failed: ${String(error)}`);
  }

  const gitRef = `https://github.com/${ctx.repo}.git`;
  const gitResult = await runCapture("git", ["ls-remote", "--tags", "--refs", gitRef], {
    shell: ctx.platform.isWin,
  });
  if (gitResult.code === 0 && gitResult.out) {
    const latestTag = pickLatestPluginTag(parseGitLsRemoteTags(gitResult.out));
    if (latestTag) {
      let updated = { ...ctx, pluginVersion: latestTag, pluginVersionExplicit: false };
      updated = syncOpenvikingVersionWithPluginVersion(updated, "latest plugin tag");
      logInfo(
        tr(
          ctx.langZh,
          `Resolved default plugin version via git tags: ${latestTag}`,
          `已通过 git tag 解析默认插件版本: ${latestTag}`,
        ),
      );
      return updated;
    }
    failures.push("git ls-remote returned no usable tags");
  } else {
    failures.push(`git ls-remote failed${gitResult.err ? `: ${gitResult.err}` : ""}`);
  }

  logError(
    tr(
      ctx.langZh,
      `Could not resolve the latest tag for ${ctx.repo}.`,
      `无法解析 ${ctx.repo} 的最新 tag。`,
    ),
  );
  console.log(
    tr(
      ctx.langZh,
      "Please rerun with --plugin-version <tag>, or use --plugin-version main to track the branch head explicitly.",
      "请使用 --plugin-version <tag> 重新执行；如果需要显式跟踪分支头，请使用 --plugin-version main。",
    ),
  );
  if (failures.length > 0) {
    logWarning(failures.join(" | "));
  }
  process.exit(1);
}

export function validateRequestedPluginVersion(ctx: InstallContext): void {
  if (!isSemverLike(ctx.pluginVersion)) return;
  if (versionGte(ctx.pluginVersion, "v0.2.7") && !versionGte(ctx.pluginVersion, "v0.2.8")) {
    logError(
      tr(ctx.langZh, "Plugin version v0.2.7 does not exist.", "插件版本 v0.2.7 不存在。"),
    );
    process.exit(1);
  }
}

export async function checkOpenClawCompatibility(ctx: InstallContext): Promise<void> {
  if (process.env.SKIP_OPENCLAW === "1") return;
  if (!ctx.pluginConfig) return;

  const { detectOpenClawVersion } = await import("./check-env.js");
  const ocVersion = await detectOpenClawVersion(ctx);
  logInfo(
    tr(ctx.langZh, `Detected OpenClaw version: ${ocVersion}`, `检测到 OpenClaw 版本: ${ocVersion}`),
  );

  if (!ctx.pluginConfig.minOpenclawVersion) return;
  if (isSemverLike(ctx.pluginVersion) && !versionGte(ctx.pluginVersion, "v0.2.8")) return;

  if (!versionGte(ocVersion, ctx.pluginConfig.minOpenclawVersion)) {
    logError(
      tr(
        ctx.langZh,
        `OpenClaw ${ocVersion} does not support this plugin (requires >= ${ctx.pluginConfig.minOpenclawVersion})`,
        `OpenClaw ${ocVersion} 不支持此插件（需要 >= ${ctx.pluginConfig.minOpenclawVersion}）`,
      ),
    );
    console.log("");
    console.log(
      tr(ctx.langZh, "Please choose one of the following options:", "请选择以下方案之一："),
    );
    console.log("");
    console.log(
      `  ${tr(ctx.langZh, "Option 1: Upgrade OpenClaw", "方案 1：升级 OpenClaw")}`,
    );
    console.log(`    npm update -g openclaw --registry ${ctx.npmRegistry}`);
    console.log("");
    console.log(
      `  ${tr(ctx.langZh, "Option 2: Install a legacy plugin release", "方案 2：安装旧版插件")}`,
    );
    console.log(`    ov-install --plugin-version <legacy-version>`);
    console.log("");
    process.exit(1);
  }
}

export function checkRequestedOpenVikingCompatibility(ctx: InstallContext): void {
  if (!ctx.pluginConfig?.minOpenvikingVersion || !ctx.openvikingVersion) return;
  if (versionGte(ctx.openvikingVersion, ctx.pluginConfig.minOpenvikingVersion)) return;

  logError(
    tr(
      ctx.langZh,
      `OpenViking ${ctx.openvikingVersion} does not support this plugin (requires >= ${ctx.pluginConfig.minOpenvikingVersion})`,
      `OpenViking ${ctx.openvikingVersion} 不支持此插件（需要 >= ${ctx.pluginConfig.minOpenvikingVersion}）`,
    ),
  );
  console.log("");
  console.log(
    tr(
      ctx.langZh,
      "Use a newer OpenViking version, or omit --openviking-version to install the latest release.",
      "请使用更新版本的 OpenViking，或省略 --openviking-version 以安装最新版本。",
    ),
  );
  process.exit(1);
}
