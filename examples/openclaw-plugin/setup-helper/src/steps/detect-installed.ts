import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type {
  InstallContext,
  InstalledPluginState,
  PluginDetection,
  PluginVariant,
  RuntimeConfig,
} from "../types.js";
import { DEFAULT_SERVER_PORT, PLUGIN_VARIANTS } from "../types.js";
import {
  readJsonFileIfExists,
  getOpenClawConfigPath,
  getInstallStatePathForPlugin,
  extractRuntimeConfigFromPluginEntry,
  readPortFromOvConf,
} from "../lib/config.js";
import { runCapture } from "../lib/process.js";

function detectPluginPresence(
  config: Record<string, unknown> | null,
  variant: PluginVariant,
  openclawDir: string,
): PluginDetection {
  const plugins = config?.plugins as Record<string, unknown> | undefined;
  const reasons: string[] = [];
  if (!plugins) {
    return { variant, present: false, reasons };
  }

  const entries = plugins.entries as Record<string, unknown> | undefined;
  if (entries && Object.prototype.hasOwnProperty.call(entries, variant.id)) {
    reasons.push("entry");
  }
  const slots = plugins.slots as Record<string, string> | undefined;
  if (slots?.[variant.slot] === variant.id) {
    reasons.push("slot");
  }
  const allow = plugins.allow as string[] | undefined;
  if (Array.isArray(allow) && allow.includes(variant.id)) {
    reasons.push("allow");
  }
  const load = plugins.load as { paths?: string[] } | undefined;
  if (
    Array.isArray(load?.paths) &&
    load!.paths.some(
      (item) =>
        typeof item === "string" && (item.includes(variant.id) || item.includes(variant.dir)),
    )
  ) {
    reasons.push("loadPath");
  }
  if (existsSync(join(openclawDir, "extensions", variant.id))) {
    reasons.push("dir");
  }

  return { variant, present: reasons.length > 0, reasons };
}

export async function detectInstalledPluginState(
  ctx: InstallContext,
): Promise<InstalledPluginState> {
  const configPath = getOpenClawConfigPath(ctx);
  const config = await readJsonFileIfExists(configPath);
  const detections: PluginDetection[] = [];

  for (const variant of PLUGIN_VARIANTS) {
    const detection = detectPluginPresence(config, variant, ctx.openclawDir);
    if (!detection.present) continue;
    detection.installState = await readJsonFileIfExists(
      getInstallStatePathForPlugin(ctx, variant.id),
    ) as PluginDetection["installState"];
    detections.push(detection);
  }

  let generation: InstalledPluginState["generation"] = "none";
  if (detections.length === 1) {
    generation = detections[0].variant.generation;
  } else if (detections.length > 1) {
    generation = "mixed";
  }

  return { config, configPath, detections, generation };
}

export function formatInstalledDetectionLabel(detection: PluginDetection): string {
  const requestedRef = detection.installState?.requestedRef;
  const releaseId = detection.installState?.releaseId;
  if (requestedRef) return `${detection.variant.id}@${requestedRef}`;
  if (releaseId) return `${detection.variant.id}#${releaseId}`;
  return `${detection.variant.id} (${detection.variant.generation}, exact version unknown)`;
}

export function formatInstalledStateLabel(installedState: InstalledPluginState): string {
  if (!installedState?.detections?.length) return "not-installed";
  return installedState.detections.map(formatInstalledDetectionLabel).join(" + ");
}

export function formatTargetVersionLabel(ctx: InstallContext): string {
  const pluginId = ctx.pluginConfig?.id || "openviking";
  const base = `${pluginId}@${ctx.pluginVersion}`;
  if (ctx.pluginConfig?.releaseId && ctx.pluginConfig.releaseId !== ctx.pluginVersion) {
    return `${base} (${ctx.pluginConfig.releaseId})`;
  }
  return base;
}

export async function prepareUpgradeRuntimeConfig(
  ctx: InstallContext,
  installedState: InstalledPluginState,
): Promise<RuntimeConfig> {
  const plugins = (installedState.config?.plugins ?? {}) as Record<string, unknown>;
  const entries = plugins.entries as Record<string, { config?: Record<string, unknown> }> | undefined;

  const candidateOrder = installedState.detections
    .map((item) => item.variant)
    .sort(
      (left, right) =>
        (right.generation === "current" ? 1 : 0) - (left.generation === "current" ? 1 : 0),
    );

  let runtime: RuntimeConfig | null = null;
  for (const variant of candidateOrder) {
    const entryConfig = extractRuntimeConfigFromPluginEntry(entries?.[variant.id]?.config);
    if (entryConfig) {
      runtime = entryConfig;
      break;
    }
  }

  if (!runtime) {
    runtime = { mode: "local", configPath: "", port: 0 };
  }

  if (runtime.mode === "remote") {
    return {
      ...runtime,
      baseUrl: runtime.baseUrl || ctx.remoteBaseUrl,
    };
  }

  const configPath = runtime.configPath || join(ctx.openvikingDir, "ov.conf");
  const port = runtime.port || (await readPortFromOvConf(configPath)) || DEFAULT_SERVER_PORT;
  return { mode: "local", configPath, port };
}

export async function detectInstalledOpenVikingVersion(ctx: InstallContext): Promise<string> {
  const pythonCandidates: string[] = [];
  const configuredPython = process.env.OPENVIKING_PYTHON?.trim();
  if (configuredPython) pythonCandidates.push(configuredPython);

  const envCandidates = ctx.platform.isWin
    ? [join(ctx.openclawDir, "openviking.env.ps1"), join(ctx.openclawDir, "openviking.env.bat")]
    : [join(ctx.openclawDir, "openviking.env")];

  for (const envPath of envCandidates) {
    if (!existsSync(envPath)) continue;
    const raw = await readFile(envPath, "utf8").catch(() => "");
    if (!raw) continue;
    const match = raw.match(/OPENVIKING_PYTHON(?:\s*=\s*|=)['"]?([^'"\r\n]+)['"]?/);
    const pythonPath = match?.[1]?.trim();
    if (pythonPath) pythonCandidates.push(pythonPath);
  }

  for (const candidate of ctx.platform.isWin
    ? ["py", "python", "python3"]
    : ["python3", "python"]) {
    pythonCandidates.push(candidate);
  }

  const seen = new Set<string>();
  for (const candidate of pythonCandidates) {
    if (!candidate || seen.has(candidate)) continue;
    seen.add(candidate);
    const result = await runCapture(
      candidate,
      ["-c", "import openviking; print(openviking.__version__)"],
      { shell: ctx.platform.isWin },
    );
    if (result.code === 0) return result.out.trim();
  }

  return "";
}
