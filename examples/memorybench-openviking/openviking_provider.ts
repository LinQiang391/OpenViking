import { createHash } from "crypto"
import type {
  Provider,
  ProviderConfig,
  IngestOptions,
  IngestResult,
  SearchOptions,
  IndexingProgressCallback,
} from "../../types/provider"
import type { UnifiedSession } from "../../types/unified"
import { logger } from "../../utils/logger"

interface OpenVikingResponse<T> {
  status: string
  result: T
}

interface OpenVikingCreateSessionResult {
  session_id: string
}

interface OpenVikingFindResult {
  memories?: OpenVikingSearchItem[]
  resources?: OpenVikingSearchItem[]
  skills?: OpenVikingSearchItem[]
  total?: number
}

interface OpenVikingSearchItem {
  uri?: string
  score?: number
  context_type?: string
  abstract?: string
  [key: string]: unknown
}

interface OpenVikingFsEntry {
  uri?: string
  isDir?: boolean
  is_dir?: boolean
  [key: string]: unknown
}

interface OpenVikingWaitResult {
  pending?: number
  in_progress?: number
  processed?: number
  errors?: number
}

export class OpenVikingProvider implements Provider {
  name = "openviking"
  concurrency = {
    default: 10,
    ingest: 1,
    indexing: 1,
    search: 5,
  }

  private baseUrl = "http://localhost:1933"
  private apiKey = ""
  private waitTimeoutSec = 30
  private memoryRoots = ["viking://user/memories", "viking://agent/memories"]

  private sessionIdsByContainer = new Map<string, Set<string>>()
  private memoryUrisByContainer = new Map<string, Set<string>>()

  async initialize(config: ProviderConfig): Promise<void> {
    if (config.baseUrl && typeof config.baseUrl === "string") {
      this.baseUrl = config.baseUrl.replace(/\/+$/, "")
    }

    if (config.apiKey && typeof config.apiKey === "string") {
      this.apiKey = config.apiKey
    }

    if (typeof config.waitTimeoutSec === "number" && Number.isFinite(config.waitTimeoutSec)) {
      this.waitTimeoutSec = Math.max(1, config.waitTimeoutSec)
    }

    if (Array.isArray(config.memoryRoots) && config.memoryRoots.length > 0) {
      this.memoryRoots = config.memoryRoots.filter(
        (v): v is string => typeof v === "string" && v.trim().length > 0
      )
    }

    await this.pingHealth()
    logger.info(`Initialized OpenViking provider (${this.baseUrl})`)
  }

  async ingest(sessions: UnifiedSession[], options: IngestOptions): Promise<IngestResult> {
    const documentIds: string[] = []

    for (const session of sessions) {
      const beforeHashes = await this.collectMemoryHashes()

      const createResult = await this.post<OpenVikingCreateSessionResult>("/api/v1/sessions")
      const openvikingSessionId = createResult.session_id
      if (!openvikingSessionId) {
        throw new Error("OpenViking returned an empty session_id")
      }

      this.trackSessionId(options.containerTag, openvikingSessionId)
      documentIds.push(openvikingSessionId)

      const sessionDate = this.getSessionDate(session)

      for (let i = 0; i < session.messages.length; i++) {
        const message = session.messages[i]
        const role = message.role === "assistant" ? "assistant" : "user"
        const content = this.decorateMessageContent({
          text: message.content,
          role,
          speaker: message.speaker,
          timestamp: message.timestamp,
          sessionDate,
          includeSessionDate: i === 0,
        })

        await this.post(`/api/v1/sessions/${openvikingSessionId}/messages`, {
          role,
          content,
        })
      }

      await this.post(`/api/v1/sessions/${openvikingSessionId}/commit`)
      await this.waitProcessed(this.waitTimeoutSec)

      const afterHashes = await this.collectMemoryHashes()
      const changedUris = this.diffHashes(beforeHashes, afterHashes)
      this.trackMemoryUris(options.containerTag, changedUris)
    }

    return { documentIds }
  }

  async awaitIndexing(
    result: IngestResult,
    _containerTag: string,
    onProgress?: IndexingProgressCallback
  ): Promise<void> {
    const documentIds = result.documentIds || []
    const total = documentIds.length

    onProgress?.({ completedIds: [], failedIds: [], total })

    if (total === 0) {
      return
    }

    try {
      await this.waitProcessed(this.waitTimeoutSec)
      onProgress?.({ completedIds: [...documentIds], failedIds: [], total })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      logger.warn(`OpenViking awaitIndexing failed: ${message}`)
      onProgress?.({ completedIds: [], failedIds: [...documentIds], total })
      throw error
    }
  }

  async search(query: string, options: SearchOptions): Promise<unknown[]> {
    const requestedLimit = options.limit || 30
    const threshold = options.threshold ?? 0.3
    const resultsByUri = new Map<string, OpenVikingSearchItem>()

    for (const root of this.memoryRoots) {
      const payload = await this.post<OpenVikingFindResult>("/api/v1/search/find", {
        query,
        target_uri: root,
        limit: requestedLimit,
        score_threshold: threshold,
      })

      const merged = [
        ...(payload.memories || []),
        ...(payload.resources || []),
        ...(payload.skills || []),
      ]

      for (const item of merged) {
        const uri = typeof item.uri === "string" ? item.uri : ""
        if (!uri) {
          continue
        }

        const existing = resultsByUri.get(uri)
        if (!existing || this.getScore(item) > this.getScore(existing)) {
          resultsByUri.set(uri, item)
        }
      }
    }

    const allResults = Array.from(resultsByUri.values()).sort(
      (a, b) => this.getScore(b) - this.getScore(a)
    )

    const trackedUris = this.memoryUrisByContainer.get(options.containerTag)
    let candidates: OpenVikingSearchItem[] = allResults
    if (trackedUris && trackedUris.size > 0) {
      const filtered = allResults.filter((item) => {
        const uri = typeof item.uri === "string" ? item.uri : ""
        return uri && trackedUris.has(uri)
      })

      if (filtered.length > 0) {
        candidates = filtered
      } else {
        logger.warn(
          `No tracked-memory hits for ${options.containerTag}; falling back to unfiltered OpenViking search`
        )
      }
    }

    const topCandidates = candidates.slice(0, requestedLimit)
    return await this.materializeOverviewResults(topCandidates)
  }

  async clear(containerTag: string): Promise<void> {
    const sessions = this.sessionIdsByContainer.get(containerTag)
    if (!sessions) {
      return
    }

    for (const sessionId of sessions) {
      try {
        await this.delete(`/api/v1/sessions/${sessionId}`)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        logger.warn(`Failed to delete session ${sessionId}: ${message}`)
      }
    }

    this.sessionIdsByContainer.delete(containerTag)
    this.memoryUrisByContainer.delete(containerTag)
  }

  private async pingHealth(): Promise<void> {
    const res = await fetch(`${this.baseUrl}/health`, { method: "GET" })
    if (!res.ok) {
      const detail = await res.text()
      throw new Error(`OpenViking health check failed (${res.status}): ${detail.slice(0, 200)}`)
    }
  }

  private decorateMessageContent(params: {
    text: string
    role: "user" | "assistant"
    speaker?: string
    timestamp?: string
    sessionDate?: string
    includeSessionDate: boolean
  }): string {
    const tags: string[] = []

    if (params.includeSessionDate && params.sessionDate) {
      tags.push(`[session_date=${params.sessionDate}]`)
    }
    if (params.timestamp) {
      tags.push(`[timestamp=${params.timestamp}]`)
    }

    const normalizedSpeaker = params.speaker?.trim()
    if (normalizedSpeaker && normalizedSpeaker.toLowerCase() !== params.role) {
      tags.push(`[speaker=${normalizedSpeaker}]`)
    }

    const prefix = tags.length > 0 ? `${tags.join(" ")}\n` : ""
    return `${prefix}${params.text}`
  }

  private getSessionDate(session: UnifiedSession): string | undefined {
    const formatted = session.metadata?.formattedDate
    if (typeof formatted === "string" && formatted.trim()) {
      return formatted.trim()
    }

    const iso = session.metadata?.date
    if (typeof iso === "string" && iso.trim()) {
      return iso.trim()
    }

    return undefined
  }

  private trackSessionId(containerTag: string, sessionId: string): void {
    let ids = this.sessionIdsByContainer.get(containerTag)
    if (!ids) {
      ids = new Set<string>()
      this.sessionIdsByContainer.set(containerTag, ids)
    }
    ids.add(sessionId)
  }

  private trackMemoryUris(containerTag: string, uris: string[]): void {
    if (uris.length === 0) {
      return
    }

    let tracked = this.memoryUrisByContainer.get(containerTag)
    if (!tracked) {
      tracked = new Set<string>()
      this.memoryUrisByContainer.set(containerTag, tracked)
    }

    for (const uri of uris) {
      tracked.add(uri)
    }
  }

  private async waitProcessed(timeoutSec: number): Promise<void> {
    const result = await this.post<OpenVikingWaitResult>("/api/v1/system/wait", {
      timeout: timeoutSec,
    })

    if ((result.errors || 0) > 0) {
      logger.warn(
        `OpenViking wait returned errors: pending=${result.pending || 0}, in_progress=${result.in_progress || 0}, errors=${result.errors || 0}`
      )
    }
  }

  private async collectMemoryHashes(): Promise<Map<string, string>> {
    const hashes = new Map<string, string>()

    for (const root of this.memoryRoots) {
      let entries: OpenVikingFsEntry[] = []
      try {
        entries = await this.get<OpenVikingFsEntry[]>("/api/v1/fs/ls", {
          uri: root,
          recursive: true,
          simple: false,
          show_all_hidden: false,
          node_limit: 5000,
        })
      } catch {
        continue
      }

      for (const entry of entries) {
        const uri = typeof entry.uri === "string" ? entry.uri : ""
        if (!uri || !uri.endsWith(".md") || uri.includes("/.")) {
          continue
        }

        const isDir = entry.isDir === true || entry.is_dir === true
        if (isDir) {
          continue
        }

        try {
          const content = await this.get<string>("/api/v1/content/read", { uri })
          const hash = createHash("sha1").update(content).digest("hex")
          hashes.set(uri, hash)
        } catch {
          // Skip unreadable files (e.g. race with memory compaction)
        }
      }
    }

    return hashes
  }

  private diffHashes(before: Map<string, string>, after: Map<string, string>): string[] {
    const changed: string[] = []

    for (const [uri, hash] of after.entries()) {
      if (before.get(uri) !== hash) {
        changed.push(uri)
      }
    }

    return changed
  }

  private getScore(item: OpenVikingSearchItem): number {
    const score = item.score
    return typeof score === "number" && Number.isFinite(score) ? score : -1
  }

  private async materializeOverviewResults(items: OpenVikingSearchItem[]): Promise<unknown[]> {
    return await Promise.all(
      items.map(async (item, index) => {
        const uri = typeof item.uri === "string" ? item.uri : ""
        const abstract = typeof item.abstract === "string" ? item.abstract : ""

        let overview = ""
        if (uri) {
          try {
            // OpenViking overview expects a directory URI. If we got a markdown file,
            // query the parent directory to avoid systematic fallback to abstract.
            const value = await this.get<unknown>("/api/v1/content/overview", {
              uri: this.toOverviewUri(uri),
            })
            overview = this.normalizeText(value)
          } catch {
            // Keep best-effort behavior and fall back to abstract.
          }
        }

        const text = overview || abstract
        return {
          rank: index + 1,
          uri,
          score: this.getScore(item),
          context_type: item.context_type || "memory",
          text,
          overview,
          abstract,
          raw: item,
        }
      })
    )
  }

  private toOverviewUri(uri: string): string {
    if (uri.endsWith(".md")) {
      const idx = uri.lastIndexOf("/")
      if (idx > 0) {
        return uri.slice(0, idx)
      }
    }
    return uri
  }

  private normalizeText(value: unknown): string {
    if (typeof value === "string") {
      return value
    }
    if (value === null || value === undefined) {
      return ""
    }
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }

  private buildHeaders(includeJson: boolean): HeadersInit {
    const headers: Record<string, string> = {}
    if (includeJson) {
      headers["Content-Type"] = "application/json"
    }
    if (this.apiKey) {
      headers["X-API-Key"] = this.apiKey
    }
    return headers
  }

  private async get<T>(path: string, query?: Record<string, string | number | boolean>): Promise<T> {
    const url = new URL(path, this.baseUrl)
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        url.searchParams.set(key, String(value))
      }
    }

    const response = await fetch(url.toString(), {
      method: "GET",
      headers: this.buildHeaders(false),
    })
    return await this.readApiResponse<T>(response, `GET ${url.pathname}`)
  }

  private async post<T>(path: string, body?: unknown): Promise<T> {
    const url = new URL(path, this.baseUrl)
    const response = await fetch(url.toString(), {
      method: "POST",
      headers: this.buildHeaders(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    return await this.readApiResponse<T>(response, `POST ${url.pathname}`)
  }

  private async delete<T>(path: string): Promise<T> {
    const url = new URL(path, this.baseUrl)
    const response = await fetch(url.toString(), {
      method: "DELETE",
      headers: this.buildHeaders(false),
    })
    return await this.readApiResponse<T>(response, `DELETE ${url.pathname}`)
  }

  private async readApiResponse<T>(response: Response, op: string): Promise<T> {
    if (!response.ok) {
      const detail = await response.text()
      throw new Error(`${op} failed (${response.status}): ${detail.slice(0, 500)}`)
    }

    let payload: OpenVikingResponse<T>
    try {
      payload = (await response.json()) as OpenVikingResponse<T>
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      throw new Error(`${op} returned non-JSON response: ${message}`)
    }

    if (!payload || payload.status !== "ok") {
      throw new Error(`${op} returned unexpected payload: ${JSON.stringify(payload)}`)
    }

    return payload.result
  }
}

export default OpenVikingProvider
