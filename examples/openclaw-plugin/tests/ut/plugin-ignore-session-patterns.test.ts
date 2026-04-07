import { describe, expect, it, vi } from "vitest";

import contextEnginePlugin from "../../index.js";

type HookHandler = (
  event: unknown,
  ctx?: { sessionId?: string; sessionKey?: string; agentId?: string },
) => unknown;

function setupPlugin(pluginConfig?: Record<string, unknown>) {
  const hooks = new Map<string, HookHandler>();
  let contextEngineFactory: (() => unknown) | null = null;

  const api = {
    pluginConfig: {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
      ingestReplyAssist: true,
      ingestReplyAssistMinSpeakerTurns: 2,
      ingestReplyAssistMinChars: 10,
      ...(pluginConfig ?? {}),
    },
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    registerTool: vi.fn(),
    registerService: vi.fn(),
    registerContextEngine: vi.fn((_id: string, factory: () => unknown) => {
      contextEngineFactory = factory;
    }),
    on: vi.fn((hookName: string, handler: HookHandler) => {
      hooks.set(hookName, handler);
    }),
  };

  contextEnginePlugin.register(api as never);

  return {
    api,
    hooks,
    createContextEngine: () => {
      if (!contextEngineFactory) {
        throw new Error("context engine factory was not registered");
      }
      return contextEngineFactory() as { commitOVSession: (sessionId: string, sessionKey?: string) => Promise<boolean> };
    },
  };
}

const TRANSCRIPT_INGEST_TEXT =
  "Alice: I prefer dark roast coffee.\n" +
  "Bob: Noted.\n" +
  "Alice: Please remember this preference for future planning.";

describe("plugin ignoreSessionPatterns hooks", () => {
  it("still applies ingest reply assist for non-ignored sessions", async () => {
    const { hooks } = setupPlugin();
    const beforePromptBuild = hooks.get("before_prompt_build");

    const result = await beforePromptBuild?.(
      {
        messages: [{ role: "user", content: TRANSCRIPT_INGEST_TEXT }],
      },
      {
        sessionId: "session-main",
        sessionKey: "agent:main:main",
      },
    ) as { prependContext?: string } | undefined;

    expect(result?.prependContext).toContain("<ingest-reply-assist>");
  });

  it("skips before_prompt_build helpers when session matches ignoreSessionPatterns", async () => {
    const { api, hooks } = setupPlugin({
      ignoreSessionPatterns: ["agent:*:cron:**"],
    });
    const beforePromptBuild = hooks.get("before_prompt_build");

    const result = await beforePromptBuild?.(
      {
        messages: [{ role: "user", content: TRANSCRIPT_INGEST_TEXT }],
      },
      {
        sessionId: "session-cron",
        sessionKey: "agent:main:cron:nightly:run:1",
      },
    );

    expect(result).toBeUndefined();
    expect(api.logger.info).toHaveBeenCalledWith(
      expect.stringContaining("skipping before_prompt_build due to ignoreSessionPatterns"),
    );
  });

  it("skips before_reset commit when session matches ignoreSessionPatterns", async () => {
    const { hooks, createContextEngine } = setupPlugin({
      ignoreSessionPatterns: ["agent:*:cron:**"],
    });
    const engine = createContextEngine();
    const commitSpy = vi.fn().mockResolvedValue(true);
    engine.commitOVSession = commitSpy;

    const beforeReset = hooks.get("before_reset");
    await beforeReset?.(
      {},
      {
        sessionId: "session-cron",
        sessionKey: "agent:main:cron:nightly:run:1",
      },
    );

    expect(commitSpy).not.toHaveBeenCalled();
  });

  it("passes sessionKey through to before_reset commit for non-ignored sessions", async () => {
    const { hooks, createContextEngine } = setupPlugin({
      ignoreSessionPatterns: ["agent:*:cron:**"],
    });
    const engine = createContextEngine();
    const commitSpy = vi.fn().mockResolvedValue(true);
    engine.commitOVSession = commitSpy;

    const beforeReset = hooks.get("before_reset");
    await beforeReset?.(
      {},
      {
        sessionId: "session-main",
        sessionKey: "agent:main:main",
      },
    );

    expect(commitSpy).toHaveBeenCalledWith("session-main", "agent:main:main");
  });
});
