import * as p from "@clack/prompts";
import pc from "picocolors";
import type { InstallContext } from "../types.js";
import { tr } from "./messages.js";

export function intro(ctx: InstallContext): void {
  p.intro(pc.bgCyan(pc.black(" 🦣 OpenViking Setup ")));
}

export function outro(message: string): void {
  p.outro(message);
}

export function logInfo(message: string): void {
  p.log.info(message);
}

export function logSuccess(message: string): void {
  p.log.success(message);
}

export function logWarning(message: string): void {
  p.log.warning(message);
}

export function logError(message: string): void {
  p.log.error(message);
}

export function logStep(message: string): void {
  p.log.step(message);
}

export function logMessage(message: string): void {
  p.log.message(message);
}

export async function selectMode(ctx: InstallContext): Promise<"local" | "remote"> {
  const result = await p.select({
    message: tr(ctx.langZh, "Select install mode", "选择安装模式"),
    options: [
      {
        value: "local" as const,
        label: tr(
          ctx.langZh,
          "Local — OpenViking runs on this machine (recommended)",
          "本地模式 — OpenViking 在本机运行（推荐）",
        ),
      },
      {
        value: "remote" as const,
        label: tr(
          ctx.langZh,
          "Remote — Connect to an existing OpenViking server",
          "远程模式 — 连接到已有的 OpenViking 服务器",
        ),
      },
    ],
    initialValue: "local" as const,
  });

  if (p.isCancel(result)) {
    p.cancel(tr(ctx.langZh, "Installation cancelled.", "安装已取消。"));
    process.exit(0);
  }
  return result;
}

export async function textInput(
  message: string,
  opts: { placeholder?: string; defaultValue?: string; validate?: (v: string) => string | void } = {},
): Promise<string> {
  const result = await p.text({
    message,
    placeholder: opts.placeholder,
    defaultValue: opts.defaultValue,
    validate: opts.validate,
  });
  if (p.isCancel(result)) {
    p.cancel("Installation cancelled.");
    process.exit(0);
  }
  return result;
}

export async function passwordInput(message: string, opts: { defaultValue?: string } = {}): Promise<string> {
  const result = await p.password({
    message,
    ...opts,
  });
  if (p.isCancel(result)) {
    p.cancel("Installation cancelled.");
    process.exit(0);
  }
  return result;
}

export async function confirmPrompt(message: string, initialValue = true): Promise<boolean> {
  const result = await p.confirm({ message, initialValue });
  if (p.isCancel(result)) {
    p.cancel("Installation cancelled.");
    process.exit(0);
  }
  return result;
}

export async function selectWorkdir(instances: string[], ctx: InstallContext): Promise<string> {
  const options = instances.map((inst, i) => ({
    value: inst,
    label: inst,
    hint: i === 0 ? "default" : undefined,
  }));

  const result = await p.select({
    message: tr(
      ctx.langZh,
      "Found multiple OpenClaw instances. Select one:",
      "发现多个 OpenClaw 实例，请选择：",
    ),
    options,
    initialValue: instances[0],
  });

  if (p.isCancel(result)) {
    p.cancel(tr(ctx.langZh, "Installation cancelled.", "安装已取消。"));
    process.exit(0);
  }
  return result;
}

export function createSpinner(): ReturnType<typeof p.spinner> {
  return p.spinner();
}
