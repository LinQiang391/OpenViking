import { readdirSync } from "node:fs";
import { join } from "node:path";
import type { InstallContext } from "../types.js";
import * as ui from "../ui/prompts.js";

export function detectOpenClawInstances(home: string): string[] {
  const instances: string[] = [];
  try {
    const entries = readdirSync(home, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (entry.name === ".openclaw" || entry.name.startsWith(".openclaw-")) {
        instances.push(join(home, entry.name));
      }
    }
  } catch {}
  return instances.sort();
}

export async function selectWorkdir(ctx: InstallContext): Promise<InstallContext> {
  if (ctx.workdirExplicit) return ctx;

  const instances = detectOpenClawInstances(ctx.platform.home);
  if (instances.length <= 1) return ctx;
  if (ctx.showCurrentVersion) {
    return { ...ctx, openclawDir: instances[0] };
  }
  if (!ctx.interactive) return ctx;

  const selected = await ui.selectWorkdir(instances, ctx);
  return { ...ctx, openclawDir: selected };
}

export async function selectMode(ctx: InstallContext): Promise<InstallContext> {
  if (!ctx.interactive) {
    return { ...ctx, mode: "local" };
  }

  const mode = await ui.selectMode(ctx);
  return { ...ctx, mode };
}
