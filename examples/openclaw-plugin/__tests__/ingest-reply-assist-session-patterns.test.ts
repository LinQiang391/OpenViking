import { describe, expect, it } from "vitest";

import { memoryOpenVikingConfigSchema } from "../config.js";
import {
  compileSessionPatterns,
  matchesSessionPattern,
  shouldIgnoreSession,
  shouldSkipIngestReplyAssistSession,
} from "../text-utils.js";

describe("ingest reply assist session patterns", () => {
  it("parses global ignore session patterns from config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      ignoreSessionPatterns: [
        "agent:*:cron:**",
        "agent:ops:maintenance:**",
      ],
    });

    expect(cfg.ignoreSessionPatterns).toEqual([
      "agent:*:cron:**",
      "agent:ops:maintenance:**",
    ]);
  });

  it("defaults global ignore session patterns to an empty list", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.ignoreSessionPatterns).toEqual([]);
  });

  it("parses ignore session patterns from config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      ingestReplyAssistIgnoreSessionPatterns: [
        "agent:*:cron:**",
        "agent:ops:maintenance:**",
      ],
    });

    expect(cfg.ingestReplyAssistIgnoreSessionPatterns).toEqual([
      "agent:*:cron:**",
      "agent:ops:maintenance:**",
    ]);
  });

  it("defaults ignore session patterns to an empty list", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.ingestReplyAssistIgnoreSessionPatterns).toEqual([]);
  });

  it("matches lossless-claw style session globs", () => {
    const patterns = compileSessionPatterns([
      "agent:*:cron:**",
      "agent:ops:maintenance:**",
    ]);

    expect(matchesSessionPattern("agent:main:cron:nightly:run:1", patterns)).toBe(true);
    expect(matchesSessionPattern("agent:ops:maintenance:weekly", patterns)).toBe(true);
    expect(matchesSessionPattern("agent:main:main", patterns)).toBe(false);
  });

  it("prefers sessionKey over sessionId when deciding whether to ignore a session", () => {
    const patterns = compileSessionPatterns(["agent:*:cron:**"]);

    expect(
      shouldIgnoreSession(
        {
          sessionId: "agent:main:cron:from-id",
          sessionKey: "agent:main:main",
        },
        patterns,
      ),
    ).toBe(false);
  });

  it("falls back to sessionId when sessionKey is unavailable for global ignore", () => {
    const patterns = compileSessionPatterns(["agent:*:cron:**"]);

    expect(
      shouldIgnoreSession(
        {
          sessionId: "agent:main:cron:nightly:run:1",
        },
        patterns,
      ),
    ).toBe(true);
  });

  it("prefers sessionKey over sessionId when deciding whether to skip assist", () => {
    const patterns = compileSessionPatterns(["agent:*:cron:**"]);

    expect(
      shouldSkipIngestReplyAssistSession(
        {
          sessionId: "agent:main:cron:from-id",
          sessionKey: "agent:main:main",
        },
        patterns,
      ),
    ).toBe(false);
  });

  it("falls back to sessionId when sessionKey is unavailable", () => {
    const patterns = compileSessionPatterns(["agent:*:cron:**"]);

    expect(
      shouldSkipIngestReplyAssistSession(
        {
          sessionId: "agent:main:cron:nightly:run:1",
        },
        patterns,
      ),
    ).toBe(true);
  });
});
