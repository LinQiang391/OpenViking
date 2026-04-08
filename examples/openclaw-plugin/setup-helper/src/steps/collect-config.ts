import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { InstallContext } from "../types.js";
import {
  DEFAULT_SERVER_PORT,
  DEFAULT_AGFS_PORT,
  DEFAULT_VLM_MODEL,
  DEFAULT_EMBED_MODEL,
} from "../types.js";
import { readPortFromOvConf } from "../lib/config.js";
import { tr } from "../ui/messages.js";
import { textInput, passwordInput, logInfo, logSuccess, confirmPrompt } from "../ui/prompts.js";

export async function collectRemoteConfig(ctx: InstallContext): Promise<InstallContext> {
  if (!ctx.interactive) return ctx;

  const baseUrl = await textInput(
    tr(ctx.langZh, "OpenViking server URL", "OpenViking 服务器地址"),
    { defaultValue: ctx.remoteBaseUrl },
  );
  const apiKey = await textInput(
    tr(ctx.langZh, "API Key (optional)", "API Key（可选）"),
    { defaultValue: ctx.remoteApiKey },
  );
  const agentId = await textInput(
    tr(ctx.langZh, "Agent ID (optional)", "Agent ID（可选）"),
    { defaultValue: ctx.remoteAgentId },
  );

  return {
    ...ctx,
    remoteBaseUrl: baseUrl,
    remoteApiKey: apiKey,
    remoteAgentId: agentId,
  };
}

export async function configureOvConf(ctx: InstallContext): Promise<InstallContext> {
  const openvikingDir = ctx.openvikingDir;
  await mkdir(openvikingDir, { recursive: true });

  const configPath = join(openvikingDir, "ov.conf");

  // If ov.conf already exists, preserve it by default
  if (existsSync(configPath)) {
    const port = (await readPortFromOvConf(configPath)) || DEFAULT_SERVER_PORT;

    if (!ctx.interactive) {
      logSuccess(
        tr(ctx.langZh, `Existing config preserved: ${configPath}`, `已保留现有配置: ${configPath}`),
      );
      return { ...ctx, selectedServerPort: port };
    }

    const reconfigure = await confirmPrompt(
      tr(
        ctx.langZh,
        `ov.conf already exists at ${configPath}. Reconfigure?`,
        `ov.conf 已存在于 ${configPath}。是否重新配置？`,
      ),
      false,
    );

    if (!reconfigure) {
      logSuccess(
        tr(ctx.langZh, `Existing config preserved: ${configPath}`, `已保留现有配置: ${configPath}`),
      );
      return { ...ctx, selectedServerPort: port };
    }
  }

  // First-time install or user chose to reconfigure
  let workspace = join(openvikingDir, "data");
  let serverPort = String(DEFAULT_SERVER_PORT);
  let agfsPort = String(DEFAULT_AGFS_PORT);
  let vlmModel = DEFAULT_VLM_MODEL;
  let embeddingModel = DEFAULT_EMBED_MODEL;
  let vlmApiKey = process.env.OPENVIKING_VLM_API_KEY || process.env.OPENVIKING_ARK_API_KEY || "";
  let embeddingApiKey =
    process.env.OPENVIKING_EMBEDDING_API_KEY || process.env.OPENVIKING_ARK_API_KEY || "";

  if (ctx.interactive) {
    vlmApiKey =
      (await passwordInput(tr(ctx.langZh, "VLM API Key (required for memory extraction)", "VLM API Key（记忆提取必需）"))) ||
      vlmApiKey;

    embeddingApiKey =
      (await passwordInput(
        tr(ctx.langZh, "Embedding API Key (press Enter to use same as VLM)", "Embedding API Key（回车使用与 VLM 相同的 Key）"),
      )) || vlmApiKey;

    const showAdvanced = await confirmPrompt(
      tr(ctx.langZh, "Show advanced options? (port, model, workspace path)", "显示高级选项？（端口、模型、数据目录）"),
      false,
    );

    if (showAdvanced) {
      workspace = await textInput(
        tr(ctx.langZh, "OpenViking workspace path", "OpenViking 数据目录"),
        { defaultValue: workspace },
      );
      serverPort = await textInput(
        tr(ctx.langZh, "OpenViking HTTP port", "OpenViking HTTP 端口"),
        { defaultValue: serverPort },
      );
      agfsPort = await textInput(tr(ctx.langZh, "AGFS port", "AGFS 端口"), {
        defaultValue: agfsPort,
      });
      vlmModel = await textInput(tr(ctx.langZh, "VLM model", "VLM 模型"), {
        defaultValue: vlmModel,
      });
      embeddingModel = await textInput(
        tr(ctx.langZh, "Embedding model", "Embedding 模型"),
        { defaultValue: embeddingModel },
      );
    }
  }

  const selectedPort = Number.parseInt(serverPort, 10) || DEFAULT_SERVER_PORT;
  const agfsPortNum = Number.parseInt(agfsPort, 10) || DEFAULT_AGFS_PORT;

  await mkdir(workspace, { recursive: true });

  const config = {
    server: {
      host: "127.0.0.1",
      port: selectedPort,
      root_api_key: null,
      cors_origins: ["*"],
    },
    storage: {
      workspace,
      vectordb: { name: "context", backend: "local", project: "default" },
      agfs: {
        port: agfsPortNum,
        log_level: "warn",
        backend: "local",
        timeout: 10,
        retry_times: 3,
      },
    },
    log: {
      level: "WARNING",
      format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
      output: "file",
      rotation: true,
      rotation_days: 3,
      rotation_interval: "midnight",
    },
    embedding: {
      dense: {
        provider: "volcengine",
        api_key: embeddingApiKey || null,
        model: embeddingModel,
        api_base: "https://ark.cn-beijing.volces.com/api/v3",
        dimension: 1024,
        input: "multimodal",
      },
    },
    vlm: {
      provider: "volcengine",
      api_key: vlmApiKey || null,
      model: vlmModel,
      api_base: "https://ark.cn-beijing.volces.com/api/v3",
      temperature: 0.1,
      max_retries: 3,
    },
  };

  await writeFile(configPath, JSON.stringify(config, null, 2) + "\n", "utf8");
  logInfo(tr(ctx.langZh, `Config generated: ${configPath}`, `已生成配置: ${configPath}`));

  return { ...ctx, selectedServerPort: selectedPort };
}
