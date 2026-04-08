import { existsSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { InstallContext, PluginVariant, RuntimeConfig } from "../types.js";
import { DEFAULT_SERVER_PORT, PLUGIN_VARIANTS } from "../types.js";
import { getOpenClawConfigPath, getOpenClawEnv, atomicWriteJson, readJsonFileIfExists } from "../lib/config.js";
import { runCapture } from "../lib/process.js";
import { tr } from "../ui/messages.js";
import { logInfo, logWarning, createSpinner } from "../ui/prompts.js";

function resolvedPluginSlotFallback(pluginId: string): string {
  if (pluginId === "memory-openviking") return "none";
  if (pluginId === "openviking") return "legacy";
  return "none";
}

function removePluginConfigFromJson(
  config: Record<string, unknown>,
  variant: PluginVariant,
): boolean {
  const plugins = config?.plugins as Record<string, unknown> | undefined;
  if (!plugins) return false;

  let changed = false;

  const allow = plugins.allow as string[] | undefined;
  if (Array.isArray(allow)) {
    const next = allow.filter((item) => item !== variant.id);
    changed = changed || next.length !== allow.length;
    plugins.allow = next;
  }

  const load = plugins.load as { paths?: string[] } | undefined;
  if (Array.isArray(load?.paths)) {
    const next = load!.paths.filter(
      (item) =>
        typeof item !== "string" || (!item.includes(variant.id) && !item.includes(variant.dir)),
    );
    changed = changed || next.length !== load!.paths.length;
    load!.paths = next;
  }

  const entries = plugins.entries as Record<string, unknown> | undefined;
  if (entries && Object.prototype.hasOwnProperty.call(entries, variant.id)) {
    delete entries[variant.id];
    changed = true;
  }

  const slots = plugins.slots as Record<string, string> | undefined;
  if (slots?.[variant.slot] === variant.id) {
    slots[variant.slot] = variant.slotFallback;
    changed = true;
  }

  return changed;
}

export async function scrubStaleOpenClawPluginRegistration(ctx: InstallContext): Promise<void> {
  const configPath = getOpenClawConfigPath(ctx);
  if (!existsSync(configPath)) return;

  const pluginId = ctx.pluginConfig!.id;
  const slot = ctx.pluginConfig!.slot;
  const slotFallback = resolvedPluginSlotFallback(pluginId);

  let raw: string;
  try {
    raw = await readFile(configPath, "utf8");
  } catch {
    return;
  }
  let cfg: Record<string, unknown>;
  try {
    cfg = JSON.parse(raw);
  } catch {
    return;
  }

  const p = cfg.plugins as Record<string, unknown> | undefined;
  if (!p) return;

  let changed = false;
  const entries = p.entries as Record<string, unknown> | undefined;
  if (entries && Object.prototype.hasOwnProperty.call(entries, pluginId)) {
    delete entries[pluginId];
    changed = true;
  }
  const allow = p.allow as string[] | undefined;
  if (Array.isArray(allow)) {
    const next = allow.filter((id) => id !== pluginId);
    if (next.length !== allow.length) {
      p.allow = next;
      changed = true;
    }
  }
  const load = p.load as { paths?: string[] } | undefined;
  if (load && Array.isArray(load.paths)) {
    const norm = (s: string) => String(s).replace(/\\/g, "/");
    const extNeedle = `/extensions/${pluginId}`;
    const next = load.paths.filter((path) => {
      if (typeof path !== "string") return true;
      return !norm(path).includes(extNeedle);
    });
    if (next.length !== load.paths.length) {
      load.paths = next;
      changed = true;
    }
  }
  const slots = p.slots as Record<string, string> | undefined;
  if (slots && slots[slot] === pluginId) {
    slots[slot] = slotFallback;
    changed = true;
  }
  if (!changed) return;
  await atomicWriteJson(configPath, cfg);
}

export async function configureOpenClawPlugin(
  ctx: InstallContext,
  opts: {
    preserveExistingConfig?: boolean;
    runtimeConfig?: RuntimeConfig | null;
    claimSlot?: boolean;
  } = {},
): Promise<void> {
  const { preserveExistingConfig = false, runtimeConfig = null, claimSlot = true } = opts;
  const s = createSpinner();
  s.start(tr(ctx.langZh, "Configuring OpenClaw plugin...", "正在配置 OpenClaw 插件..."));

  const pluginId = ctx.pluginConfig!.id;
  const pluginSlot = ctx.pluginConfig!.slot;
  const configPath = getOpenClawConfigPath(ctx);

  // Read existing openclaw.json (or start fresh)
  let config = (await readJsonFileIfExists(configPath)) as Record<string, unknown> || {};

  // Ensure plugins structure exists
  const plugins = (config.plugins ?? {}) as Record<string, unknown>;
  config.plugins = plugins;
  plugins.entries = (plugins.entries ?? {}) as Record<string, unknown>;
  plugins.slots = (plugins.slots ?? {}) as Record<string, unknown>;
  plugins.allow = (plugins.allow ?? []) as string[];
  plugins.load = (plugins.load ?? { paths: [] }) as { paths: string[] };
  if (!Array.isArray((plugins.load as { paths: string[] }).paths)) {
    (plugins.load as { paths: string[] }).paths = [];
  }

  const entries = plugins.entries as Record<string, unknown>;
  const slots = plugins.slots as Record<string, string>;
  const allow = plugins.allow as string[];
  const loadPaths = (plugins.load as { paths: string[] }).paths;

  if (!preserveExistingConfig) {
    // Scrub stale registrations for this plugin before re-registering
    const idx = allow.indexOf(pluginId);
    if (idx >= 0) allow.splice(idx, 1);
    delete entries[pluginId];
    const extNeedle = `/extensions/${pluginId}`;
    const norm = (s: string) => String(s).replace(/\\/g, "/");
    (plugins.load as { paths: string[] }).paths = loadPaths.filter(
      (p) => typeof p !== "string" || !norm(p).includes(extNeedle),
    );
  }

  // Register plugin: add to allow list
  if (!allow.includes(pluginId)) {
    allow.push(pluginId);
  }

  // Register plugin: add to load paths
  const extPath = resolve(join(ctx.openclawDir, "extensions", pluginId));
  const currentLoadPaths = (plugins.load as { paths: string[] }).paths;
  if (!currentLoadPaths.some((p) => resolve(p) === extPath)) {
    currentLoadPaths.push(extPath);
  }

  // Set slot
  if (claimSlot) {
    slots[pluginSlot] = pluginId;
  } else {
    logWarning(
      tr(
        ctx.langZh,
        `Skipped claiming plugins.slots.${pluginSlot}; it is currently owned by another plugin.`,
        `已跳过设置 plugins.slots.${pluginSlot}，当前该 slot 由其他插件占用。`,
      ),
    );
  }

  if (preserveExistingConfig) {
    // Only write the registration (allow, load, slot), keep existing entry config
    await atomicWriteJson(configPath, config);
    s.stop(
      tr(
        ctx.langZh,
        `Preserved existing plugin runtime config for ${pluginId}`,
        `已保留 ${pluginId} 的现有插件运行时配置`,
      ),
    );
    return;
  }

  // Build runtime config for the entry
  const effectiveConfig: RuntimeConfig = runtimeConfig || (
    ctx.mode === "remote"
      ? {
          mode: "remote",
          baseUrl: ctx.remoteBaseUrl,
          apiKey: ctx.remoteApiKey,
          agentId: ctx.remoteAgentId,
        }
      : {
          mode: "local",
          configPath: join(ctx.openvikingDir, "ov.conf"),
          port: ctx.selectedServerPort,
        }
  );

  let entryConfig: Record<string, unknown>;
  if (effectiveConfig.mode === "local") {
    entryConfig = {
      mode: "local",
      configPath: effectiveConfig.configPath || join(ctx.openvikingDir, "ov.conf"),
      port: effectiveConfig.port || DEFAULT_SERVER_PORT,
    };
  } else {
    entryConfig = {
      mode: "remote",
      baseUrl: effectiveConfig.baseUrl || ctx.remoteBaseUrl,
      ...(effectiveConfig.apiKey && { apiKey: effectiveConfig.apiKey }),
      ...(effectiveConfig.agentId && { agentId: effectiveConfig.agentId }),
    };
  }

  // Legacy memory plugins need extra fields
  if (ctx.pluginConfig!.kind === "memory") {
    entryConfig.targetUri = "viking://user/memories";
    entryConfig.autoRecall = true;
    entryConfig.autoCapture = true;
  }

  entries[pluginId] = { config: entryConfig };

  // Atomic write: build complete config in memory, write once via rename
  await atomicWriteJson(configPath, config);

  s.stop(tr(ctx.langZh, "OpenClaw plugin configured ✓", "OpenClaw 插件配置完成 ✓"));
}

export async function cleanupInstalledPluginConfig(
  ctx: InstallContext,
): Promise<void> {
  const installedState = ctx.installedUpgradeState;
  if (!installedState?.config || !(installedState.config as Record<string, unknown>).plugins) {
    logWarning(
      tr(
        ctx.langZh,
        "openclaw.json has no plugins section; skipped targeted plugin cleanup",
        "openclaw.json 中没有 plugins 配置，已跳过定向插件清理",
      ),
    );
    return;
  }

  const nextConfig = structuredClone(installedState.config) as Record<string, unknown>;
  let changed = false;
  for (const detection of installedState.detections) {
    changed = removePluginConfigFromJson(nextConfig, detection.variant) || changed;
  }

  if (!changed) {
    logInfo(
      tr(
        ctx.langZh,
        "No OpenViking plugin config changes were required",
        "无需修改 OpenViking 插件配置",
      ),
    );
    return;
  }

  await writeFile(installedState.configPath, `${JSON.stringify(nextConfig, null, 2)}\n`, "utf8");
  logInfo(
    tr(
      ctx.langZh,
      "Cleaned existing OpenViking plugin config only",
      "已仅清理 OpenViking 自身插件配置",
    ),
  );
}
