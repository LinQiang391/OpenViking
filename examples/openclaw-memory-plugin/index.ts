import { spawn, execSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir, tmpdir, platform } from "node:os";

const IS_WIN = platform() === "win32";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { Type } from "@sinclair/typebox";
import { memoryOpenVikingConfigSchema } from "./config.js";

type FindResultItem = {
  uri: string;
  is_leaf?: boolean;
  abstract?: string;
  overview?: string;
  category?: string;
  score?: number;
  match_reason?: string;
};

type FindResult = {
  memories?: FindResultItem[];
  resources?: FindResultItem[];
  skills?: FindResultItem[];
  total?: number;
};

const MEMORY_URI_PREFIXES = ["viking://user/memories", "viking://agent/memories"];

const MEMORY_TRIGGERS = [
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

function getCaptureDecision(text: string): { shouldCapture: boolean; reason: string } {
  // Strip injected memory context before evaluating — the user's actual text follows after it.
  const stripped = text.replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>\s*/g, "").trim();
  if (stripped.length < 10 || stripped.length > 1000) {
    return { shouldCapture: false, reason: "length_out_of_range" };
  }
  for (const trigger of MEMORY_TRIGGERS) {
    if (trigger.test(stripped)) {
      return { shouldCapture: true, reason: `matched_trigger:${trigger.toString()}` };
    }
  }
  return { shouldCapture: false, reason: "no_trigger_matched" };
}

function clampScore(value: number | undefined): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function isMemoryUri(uri: string): boolean {
  return MEMORY_URI_PREFIXES.some((prefix) => uri.startsWith(prefix));
}

function normalizeDedupeText(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function isEventOrCaseMemory(item: FindResultItem): boolean {
  const category = (item.category ?? "").toLowerCase();
  const uri = item.uri.toLowerCase();
  return (
    category === "events" ||
    category === "cases" ||
    uri.includes("/events/") ||
    uri.includes("/cases/")
  );
}

function getMemoryDedupeKey(item: FindResultItem): string {
  const abstract = normalizeDedupeText(item.abstract ?? item.overview ?? "");
  const category = (item.category ?? "").toLowerCase() || "unknown";
  if (abstract && !isEventOrCaseMemory(item)) {
    return `abstract:${category}:${abstract}`;
  }
  return `uri:${item.uri}`;
}

function postProcessMemories(
  items: FindResultItem[],
  options: {
    limit: number;
    scoreThreshold: number;
    leafOnly?: boolean;
  },
): FindResultItem[] {
  const deduped: FindResultItem[] = [];
  const seen = new Set<string>();
  const sorted = [...items].sort((a, b) => clampScore(b.score) - clampScore(a.score));
  for (const item of sorted) {
    if (options.leafOnly && item.is_leaf !== true) {
      continue;
    }
    if (clampScore(item.score) < options.scoreThreshold) {
      continue;
    }
    const key = getMemoryDedupeKey(item);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(item);
    if (deduped.length >= options.limit) {
      break;
    }
  }
  return deduped;
}

function formatMemoryLines(items: FindResultItem[]): string {
  return items
    .map((item, index) => {
      const score = clampScore(item.score);
      const abstract = item.abstract?.trim() || item.overview?.trim() || item.uri;
      const category = item.category ?? "memory";
      return `${index + 1}. [${category}] ${abstract} (${(score * 100).toFixed(0)}%)`;
    })
    .join("\n");
}

function isPreferencesMemory(item: FindResultItem): boolean {
  return (
    item.category === "preferences" ||
    item.uri.includes("/preferences/") ||
    item.uri.endsWith("/preferences")
  );
}

function rankForInjection(item: FindResultItem): number {
  // Prefer concrete memory leaves; prefer user preferences when scores are close.
  const baseScore = clampScore(item.score);
  const leafBoost = item.is_leaf ? 1 : 0;
  const preferenceBoost = isPreferencesMemory(item) ? 0.05 : 0;
  return baseScore + leafBoost + preferenceBoost;
}

function pickMemoriesForInjection(items: FindResultItem[], limit: number): FindResultItem[] {
  if (items.length === 0 || limit <= 0) {
    return [];
  }

  const sorted = [...items].sort((a, b) => rankForInjection(b) - rankForInjection(a));
  const deduped: FindResultItem[] = [];
  const seen = new Set<string>();
  for (const item of sorted) {
    const abstractKey = (item.abstract ?? item.overview ?? "").trim().toLowerCase();
    const key = abstractKey || item.uri;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(item);
  }
  const leaves = deduped.filter((item) => item.is_leaf);
  if (leaves.length >= limit) {
    return leaves.slice(0, limit);
  }

  const picked = [...leaves];
  const used = new Set(leaves.map((item) => item.uri));
  for (const item of deduped) {
    if (picked.length >= limit) {
      break;
    }
    if (used.has(item.uri)) {
      continue;
    }
    picked.push(item);
  }
  return picked;
}

class OpenVikingClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly timeoutMs: number,
  ) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      if (this.apiKey) {
        headers.set("X-API-Key", this.apiKey);
      }
      if (init.body && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }

      const response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
      });

      const payload = (await response.json().catch(() => ({}))) as {
        status?: string;
        result?: T;
        error?: { code?: string; message?: string };
      };

      if (!response.ok || payload.status === "error") {
        const code = payload.error?.code ? ` [${payload.error.code}]` : "";
        const message = payload.error?.message ?? `HTTP ${response.status}`;
        throw new Error(`OpenViking request failed${code}: ${message}`);
      }

      return (payload.result ?? payload) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  async healthCheck(): Promise<void> {
    await this.request<{ status: string }>("/health");
  }

  async find(
    query: string,
    options: {
      targetUri: string;
      limit: number;
      scoreThreshold?: number;
      sessionId?: string;
    },
  ): Promise<FindResult> {
    const body = {
      query,
      target_uri: options.targetUri,
      limit: options.limit,
      score_threshold: options.scoreThreshold,
      session_id: options.sessionId,
    };
    return this.request<FindResult>("/api/v1/search/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async createSession(): Promise<string> {
    const result = await this.request<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    return result.session_id;
  }

  async addSessionMessage(sessionId: string, role: string, content: string): Promise<void> {
    await this.request<{ session_id: string }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ role, content }),
      },
    );
  }

  async extractSessionMemories(sessionId: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`,
      { method: "POST", body: JSON.stringify({}) },
    );
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  }

  async deleteUri(uri: string): Promise<void> {
    await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
      method: "DELETE",
    });
  }
}

function extractTextsFromUserMessages(messages: unknown[]): string[] {
  const texts: string[] = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") {
      continue;
    }
    const msgObj = msg as Record<string, unknown>;
    if (msgObj.role !== "user") {
      continue;
    }
    const content = msgObj.content;
    if (typeof content === "string") {
      texts.push(content);
      continue;
    }
    if (Array.isArray(content)) {
      for (const block of content) {
        if (!block || typeof block !== "object") {
          continue;
        }
        const blockObj = block as Record<string, unknown>;
        if (blockObj.type === "text" && typeof blockObj.text === "string") {
          texts.push(blockObj.text);
        }
      }
    }
  }
  return texts;
}

function waitForHealth(baseUrl: string, timeoutMs: number, intervalMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (Date.now() > deadline) {
        reject(new Error(`OpenViking health check timeout at ${baseUrl}`));
        return;
      }
      fetch(`${baseUrl}/health`)
        .then((r) => r.json())
        .then((body: { status?: string }) => {
          if (body?.status === "ok") {
            resolve();
            return;
          }
          setTimeout(tick, intervalMs);
        })
        .catch(() => setTimeout(tick, intervalMs));
    };
    tick();
  });
}

const memoryPlugin = {
  id: "memory-openviking",
  name: "Memory (OpenViking)",
  description: "OpenViking-backed long-term memory with auto-recall/capture",
  kind: "memory" as const,
  configSchema: memoryOpenVikingConfigSchema,

  register(api: OpenClawPluginApi) {
    const cfg = memoryOpenVikingConfigSchema.parse(api.pluginConfig);

    let clientPromise: Promise<OpenVikingClient>;
    let localProcess: ReturnType<typeof spawn> | null = null;
    let resolveLocalClient: ((c: OpenVikingClient) => void) | null = null;

    if (cfg.mode === "local") {
      clientPromise = new Promise<OpenVikingClient>((resolve) => {
        resolveLocalClient = resolve;
      });
    } else {
      clientPromise = Promise.resolve(new OpenVikingClient(cfg.baseUrl, cfg.apiKey, cfg.timeoutMs));
    }

    const getClient = (): Promise<OpenVikingClient> => clientPromise;

    api.registerTool(
      {
        name: "memory_recall",
        label: "Memory Recall (OpenViking)",
        description:
          "Search long-term memories from OpenViking. Use when you need past user preferences, facts, or decisions.",
        parameters: Type.Object({
          query: Type.String({ description: "Search query" }),
          limit: Type.Optional(
            Type.Number({ description: "Max results (default: plugin config)" }),
          ),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId, params) {
          const { query } = params as { query: string };
          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : cfg.recallLimit;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : cfg.targetUri;
          const requestLimit = Math.max(limit * 4, limit);
          const result = await (await getClient()).find(query, {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
          });
          const memories = postProcessMemories(result.memories ?? [], {
            limit,
            scoreThreshold,
          });
          if (memories.length === 0) {
            return {
              content: [{ type: "text", text: "No relevant OpenViking memories found." }],
              details: { count: 0, total: result.total ?? 0, scoreThreshold },
            };
          }
          return {
            content: [
              {
                type: "text",
                text: `Found ${memories.length} memories:\n\n${formatMemoryLines(memories)}`,
              },
            ],
            details: {
              count: memories.length,
              memories,
              total: result.total ?? memories.length,
              scoreThreshold,
              requestLimit,
            },
          };
        },
      },
      { name: "memory_recall" },
    );

    api.registerTool(
      {
        name: "memory_store",
        label: "Memory Store (OpenViking)",
        description:
          "Store text in OpenViking memory pipeline by writing to a session and running memory extraction.",
        parameters: Type.Object({
          text: Type.String({ description: "Information to store as memory source text" }),
          role: Type.Optional(Type.String({ description: "Session role, default user" })),
          sessionId: Type.Optional(Type.String({ description: "Existing OpenViking session ID" })),
        }),
        async execute(_toolCallId, params) {
          const { text } = params as { text: string };
          const role =
            typeof (params as { role?: string }).role === "string"
              ? (params as { role: string }).role
              : "user";
          const sessionIdIn = (params as { sessionId?: string }).sessionId;

          api.logger.info?.(
            `memory-openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${sessionIdIn ?? "temp"})`,
          );

          let sessionId = sessionIdIn;
          let createdTempSession = false;
          try {
            const c = await getClient();
            if (!sessionId) {
              sessionId = await c.createSession();
              createdTempSession = true;
            }
            await c.addSessionMessage(sessionId, role, text);
            const extracted = await c.extractSessionMemories(sessionId);
            if (extracted.length === 0) {
              api.logger.warn(
                `memory-openviking: memory_store completed but extract returned 0 memories (sessionId=${sessionId}). ` +
                  "Check OpenViking server logs for embedding/extract errors (e.g. 401 API key, or extraction pipeline).",
              );
            } else {
              api.logger.info?.(`memory-openviking: memory_store extracted ${extracted.length} memories`);
            }
            return {
              content: [
                {
                  type: "text",
                  text: `Stored in OpenViking session ${sessionId} and extracted ${extracted.length} memories.`,
                },
              ],
              details: { action: "stored", sessionId, extractedCount: extracted.length, extracted },
            };
          } catch (err) {
            api.logger.warn(`memory-openviking: memory_store failed: ${String(err)}`);
            throw err;
          } finally {
            if (createdTempSession && sessionId) {
              const c = await getClient().catch(() => null);
              if (c) await c.deleteSession(sessionId!).catch(() => {});
            }
          }
        },
      },
      { name: "memory_store" },
    );

    api.registerTool(
      {
        name: "memory_forget",
        label: "Memory Forget (OpenViking)",
        description:
          "Forget memory by URI, or search then delete when a strong single match is found.",
        parameters: Type.Object({
          uri: Type.Optional(Type.String({ description: "Exact memory URI to delete" })),
          query: Type.Optional(Type.String({ description: "Search query to find memory URI" })),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
          limit: Type.Optional(Type.Number({ description: "Search limit (default: 5)" })),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId, params) {
          const uri = (params as { uri?: string }).uri;
          if (uri) {
            if (!isMemoryUri(uri)) {
              return {
                content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }],
                details: { action: "rejected", uri },
              };
            }
            await (await getClient()).deleteUri(uri);
            return {
              content: [{ type: "text", text: `Forgotten: ${uri}` }],
              details: { action: "deleted", uri },
            };
          }

          const query = (params as { query?: string }).query;
          if (!query) {
            return {
              content: [{ type: "text", text: "Provide uri or query." }],
              details: { error: "missing_param" },
            };
          }

          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : 5;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : cfg.targetUri;
          const requestLimit = Math.max(limit * 4, 20);

          const result = await (await getClient()).find(query, {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
          });
          const candidates = postProcessMemories(result.memories ?? [], {
            limit: requestLimit,
            scoreThreshold,
            leafOnly: true,
          }).filter((item) => isMemoryUri(item.uri));
          if (candidates.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: "No matching leaf memory candidates found. Try a more specific query.",
                },
              ],
              details: { action: "none", scoreThreshold },
            };
          }
          const top = candidates[0];
          if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
            await (await getClient()).deleteUri(top.uri);
            return {
              content: [{ type: "text", text: `Forgotten: ${top.uri}` }],
              details: { action: "deleted", uri: top.uri, score: top.score ?? 0 },
            };
          }

          const list = candidates
            .map((item) => `- ${item.uri} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
            .join("\n");

          return {
            content: [
              {
                type: "text",
                text: `Found ${candidates.length} candidates. Specify uri:\n${list}`,
              },
            ],
            details: { action: "candidates", candidates, scoreThreshold, requestLimit },
          };
        },
      },
      { name: "memory_forget" },
    );

    if (cfg.autoRecall) {
      api.on("before_agent_start", async (event) => {
        if (!event.prompt || event.prompt.length < 5) {
          return;
        }
        try {
          const candidateLimit = Math.max(cfg.recallLimit * 4, cfg.recallLimit);
          const result = await (await getClient()).find(event.prompt, {
            targetUri: cfg.targetUri,
            limit: candidateLimit,
            scoreThreshold: 0,
          });
          const processed = postProcessMemories(result.memories ?? [], {
            limit: candidateLimit,
            scoreThreshold: cfg.recallScoreThreshold,
          });
          const memories = pickMemoriesForInjection(processed, cfg.recallLimit);
          if (memories.length === 0) {
            return;
          }
          const memoryContext = memories
            .map((item) => `- [${item.category ?? "memory"}] ${item.abstract ?? item.uri}`)
            .join("\n");
          api.logger.info?.(
            `memory-openviking: injecting ${memories.length} memories into context`,
          );
          return {
            prependContext:
              "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
              `${memoryContext}\n` +
              "</relevant-memories>",
          };
        } catch (err) {
          api.logger.warn(`memory-openviking: auto-recall failed: ${String(err)}`);
        }
      });
    }

    if (cfg.autoCapture) {
      api.on("agent_end", async (event) => {
        if (!event.success || !event.messages || event.messages.length === 0) {
          api.logger.info(
            `memory-openviking: auto-capture skipped (success=${String(event.success)}, messages=${event.messages?.length ?? 0})`,
          );
          return;
        }
        try {
          const texts = extractTextsFromUserMessages(event.messages);
          api.logger.info(
            `memory-openviking: auto-capture evaluating ${texts.length} text candidates`,
          );
          const decisions = texts
            .map((text) => ({ text, decision: getCaptureDecision(text) }))
            .filter((item) => item.text);
          for (const item of decisions.slice(0, 5)) {
            const preview = item.text.length > 80 ? `${item.text.slice(0, 80)}...` : item.text;
            api.logger.info(
              `memory-openviking: capture-check shouldCapture=${String(item.decision.shouldCapture)} reason=${item.decision.reason} text="${preview}"`,
            );
          }
          const toCapture = decisions
            .filter((item) => item.decision.shouldCapture)
            .map((item) => item.text)
            .slice(0, 3);
          if (toCapture.length === 0) {
            api.logger.info("memory-openviking: auto-capture skipped (no matched texts)");
            return;
          }
          const c = await getClient();
          const sessionId = await c.createSession();
          try {
            for (const text of toCapture) {
              await c.addSessionMessage(sessionId, "user", text);
            }
            const extracted = await c.extractSessionMemories(sessionId);
            api.logger.info(
              `memory-openviking: auto-captured ${toCapture.length} messages, extracted ${extracted.length} memories`,
            );
            if (extracted.length === 0) {
              api.logger.warn(
                "memory-openviking: auto-capture completed but extract returned 0 memories. Check OpenViking server logs for embedding/extract errors.",
              );
            }
          } finally {
            await c.deleteSession(sessionId).catch(() => {});
          }
        } catch (err) {
          api.logger.warn(`memory-openviking: auto-capture failed: ${String(err)}`);
        }
      });
    }

    api.registerService({
      id: "memory-openviking",
      start: async () => {
        if (cfg.mode === "local" && resolveLocalClient) {
          const baseUrl = cfg.baseUrl;
          // Local mode: startup (embedder load, AGFS) can take 1–2 min; use longer health timeout
          const timeoutMs = Math.max(cfg.timeoutMs, 120_000);
          const intervalMs = 500;
          const defaultPy = IS_WIN ? "python" : "python3";
          let pythonCmd = process.env.OPENVIKING_PYTHON;
          if (!pythonCmd) {
            if (IS_WIN) {
              const envBat = join(homedir(), ".openclaw", "openviking.env.bat");
              if (existsSync(envBat)) {
                try {
                  const content = readFileSync(envBat, "utf-8");
                  const m = content.match(/set\s+OPENVIKING_PYTHON=(.+)/i);
                  if (m?.[1]) pythonCmd = m[1].trim();
                } catch { /* ignore */ }
              }
            } else {
              const envFile = join(homedir(), ".openclaw", "openviking.env");
              if (existsSync(envFile)) {
                try {
                  const content = readFileSync(envFile, "utf-8");
                  const m = content.match(/OPENVIKING_PYTHON=['"]([^'"]+)['"]/);
                  if (m?.[1]) pythonCmd = m[1];
                } catch {
                  /* ignore */
                }
              }
            }
          }
          if (!pythonCmd) {
            if (IS_WIN) {
              try {
                pythonCmd = execSync("where python", { encoding: "utf-8", shell: true }).split(/\r?\n/)[0].trim();
              } catch {
                pythonCmd = "python";
              }
            } else {
              try {
                pythonCmd = execSync("command -v python3 || which python3", {
                  encoding: "utf-8",
                  env: process.env,
                  shell: "/bin/sh",
                }).trim();
              } catch {
                pythonCmd = "python3";
              }
            }
          }
          if (pythonCmd === defaultPy) {
            api.logger.warn?.(
              `memory-openviking: 未解析到 ${defaultPy} 路径，将用 "${defaultPy}"。若 openviking 在自定义 Python 下，请设置 OPENVIKING_PYTHON` +
              (IS_WIN ? ' 或 call "%USERPROFILE%\\.openclaw\\openviking.env.bat"' : " 或 source ~/.openclaw/openviking.env"),
            );
          }
          // Kill stale OpenViking processes occupying the target port
          if (IS_WIN) {
            try {
              const netstatOut = execSync(`netstat -ano | findstr "LISTENING" | findstr ":${cfg.port}"`, {
                encoding: "utf-8", shell: true,
              }).trim();
              if (netstatOut) {
                const pids = new Set<number>();
                for (const line of netstatOut.split(/\r?\n/)) {
                  const m = line.trim().match(/\s(\d+)\s*$/);
                  if (m) pids.add(Number(m[1]));
                }
                for (const pid of pids) {
                  if (pid > 0) {
                    api.logger.info?.(`memory-openviking: killing stale process on port ${cfg.port} (pid ${pid})`);
                    try { execSync(`taskkill /PID ${pid} /F`, { shell: true }); } catch { /* already gone */ }
                  }
                }
                await new Promise((r) => setTimeout(r, 500));
              }
            } catch { /* netstat not available or no stale process */ }
          } else {
            try {
              const lsofOut = execSync(`lsof -ti tcp:${cfg.port} -s tcp:listen 2>/dev/null || true`, {
                encoding: "utf-8",
                shell: "/bin/sh",
              }).trim();
              if (lsofOut) {
                for (const pidStr of lsofOut.split(/\s+/)) {
                  const pid = Number(pidStr);
                  if (pid > 0) {
                    api.logger.info?.(`memory-openviking: killing stale process on port ${cfg.port} (pid ${pid})`);
                    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
                  }
                }
                await new Promise((r) => setTimeout(r, 500));
              }
            } catch { /* lsof not available or no stale process */ }
          }

          // Inherit system environment; optionally override Go/Python paths via env vars
          const pathSep = IS_WIN ? ";" : ":";
          const env = {
            ...process.env,
            OPENVIKING_CONFIG_FILE: cfg.configPath,
            ...(process.env.OPENVIKING_GO_PATH && { PATH: `${process.env.OPENVIKING_GO_PATH}${pathSep}${process.env.PATH || ""}` }),
            ...(process.env.OPENVIKING_GOPATH && { GOPATH: process.env.OPENVIKING_GOPATH }),
            ...(process.env.OPENVIKING_GOPROXY && { GOPROXY: process.env.OPENVIKING_GOPROXY }),
          };
          const child = spawn(
            pythonCmd,
            [
              "-m",
              "openviking.server.bootstrap",
              "--config",
              cfg.configPath,
              "--host",
              "127.0.0.1",
              "--port",
              String(cfg.port),
            ],
            { env, cwd: IS_WIN ? tmpdir() : "/tmp", stdio: ["ignore", "pipe", "pipe"] },
          );
          localProcess = child;
          child.on("error", (err) => api.logger.warn(`memory-openviking: local server error: ${String(err)}`));
          child.stderr?.on("data", (chunk) => api.logger.debug?.(`[openviking] ${String(chunk).trim()}`));
          try {
            await waitForHealth(baseUrl, timeoutMs, intervalMs);
            const client = new OpenVikingClient(baseUrl, cfg.apiKey, cfg.timeoutMs);
            resolveLocalClient(client);
            api.logger.info(
              `memory-openviking: local server started (${baseUrl}, config: ${cfg.configPath})`,
            );
          } catch (err) {
            localProcess = null;
            child.kill("SIGTERM");
            throw err;
          }
        } else {
          await (await getClient()).healthCheck().catch(() => {});
          api.logger.info(
            `memory-openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`,
          );
        }
      },
      stop: () => {
        if (localProcess) {
          localProcess.kill("SIGTERM");
          localProcess = null;
          api.logger.info("memory-openviking: local server stopped");
        } else {
          api.logger.info("memory-openviking: stopped");
        }
      },
    });
  },
};

export default memoryPlugin;
