import type { InstallContext } from "../types.js";
import { readJsonFileIfExists, getInstallStatePathForPlugin } from "../lib/config.js";
import { detectInstalledOpenVikingVersion } from "../steps/detect-installed.js";
import { logStep } from "../ui/prompts.js";
import { tr } from "../ui/messages.js";

export async function printCurrentVersionInfo(ctx: InstallContext): Promise<void> {
  const state = await readJsonFileIfExists(getInstallStatePathForPlugin(ctx, "openviking")) as
    | { requestedRef?: string; releaseId?: string; installedAt?: string }
    | null;

  const pluginRequestedRef = state?.requestedRef || "";
  const pluginReleaseId = state?.releaseId || "";
  const pluginInstalledAt = state?.installedAt || "";
  const openvikingInstalledVersion = await detectInstalledOpenVikingVersion(ctx);

  const lines = [
    tr(ctx.langZh, "Installed versions", "当前已安装版本"),
    "",
    `Target: ${ctx.openclawDir}`,
    `Plugin: ${pluginReleaseId || pluginRequestedRef || "not installed"}`,
  ];

  if (pluginRequestedRef && pluginReleaseId && pluginRequestedRef !== pluginReleaseId) {
    lines.push(`Plugin requested ref: ${pluginRequestedRef}`);
  }
  lines.push(`OpenViking: ${openvikingInstalledVersion || "unknown"}`);
  if (pluginInstalledAt) {
    lines.push(`Installed at: ${pluginInstalledAt}`);
  }

  logStep(lines.join("\n"));
}
