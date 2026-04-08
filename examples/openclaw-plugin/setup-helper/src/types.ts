export interface PlatformInfo {
  os: "windows" | "macos" | "linux";
  shell: "powershell" | "bash" | "zsh" | "unknown";
  home: string;
  isWin: boolean;
}

export interface EnvironmentCheck {
  python: { ok: boolean; version: string; path: string };
  node: { ok: boolean; version: string };
  openclaw: { ok: boolean; version: string };
}

export interface ResolvedPluginConfig {
  dir: string;
  id: string;
  kind: string;
  slot: string;
  files: { required: string[]; optional: string[] };
  npmOmitDev: boolean;
  minOpenclawVersion: string;
  minOpenvikingVersion: string;
  releaseId: string;
}

export interface LocalConfig {
  mode: "local";
  configPath: string;
  port: number;
}

export interface RemoteConfig {
  mode: "remote";
  baseUrl: string;
  apiKey: string;
  agentId: string;
}

export type RuntimeConfig = LocalConfig | RemoteConfig;

export interface EnvFiles {
  shellPath: string;
  powershellPath?: string;
}

export interface PluginVariant {
  dir: string;
  id: string;
  kind: string;
  slot: string;
  required: string[];
  optional: string[];
  generation: "legacy" | "current";
  slotFallback: string;
}

export interface PluginDetection {
  variant: PluginVariant;
  present: boolean;
  reasons: string[];
  installState?: InstallState | null;
}

export interface InstalledPluginState {
  config: Record<string, unknown> | null;
  configPath: string;
  detections: PluginDetection[];
  generation: "none" | "legacy" | "current" | "mixed";
}

export interface InstallState {
  pluginId: string;
  generation: string;
  requestedRef: string;
  releaseId: string;
  operation: string;
  fromVersion: string;
  configBackupPath: string;
  pluginBackups: PluginBackup[];
  installedAt: string;
  repo: string;
}

export interface PluginBackup {
  pluginId: string;
  backupDir: string;
}

export interface UpgradeAudit {
  operation: string;
  createdAt: string;
  fromVersion: string;
  toVersion: string;
  configBackupPath: string;
  pluginBackups: PluginBackup[];
  runtimeMode: string;
  completedAt?: string;
  rolledBackAt?: string;
  rollbackConfigPath?: string;
}

export interface CaptureResult {
  code: number | null;
  out: string;
  err: string;
}

export interface PipInstallResult {
  result: CaptureResult;
  usedFallback: boolean;
  primaryResult?: CaptureResult;
  fallbackResult?: CaptureResult;
}

export interface InstallContext {
  repo: string;
  pluginVersion: string;
  pluginVersionExplicit: boolean;
  openvikingVersion: string;
  openvikingRepo: string;
  openclawDir: string;
  defaultOpenclawDir: string;
  openvikingDir: string;
  interactive: boolean;
  langZh: boolean;
  workdirExplicit: boolean;

  platform: PlatformInfo;
  pluginConfig: ResolvedPluginConfig | null;
  pluginDest: string;
  mode: "local" | "remote";
  runtimeConfig: RuntimeConfig | null;

  pythonPath: string;
  envFiles: EnvFiles | null;

  upgradePluginOnly: boolean;
  rollbackLastUpgrade: boolean;
  showCurrentVersion: boolean;

  upgradeRuntimeConfig: RuntimeConfig | null;
  installedUpgradeState: InstalledPluginState | null;
  upgradeAudit: UpgradeAudit | null;

  remoteBaseUrl: string;
  remoteApiKey: string;
  remoteAgentId: string;
  selectedServerPort: number;

  npmRegistry: string;
  pipIndexUrl: string;
}

export const DEFAULT_SERVER_PORT = 1933;
export const DEFAULT_AGFS_PORT = 1833;
export const DEFAULT_VLM_MODEL = "doubao-seed-2-0-pro-260215";
export const DEFAULT_EMBED_MODEL = "doubao-embedding-vision-251215";
export const DEFAULT_PIP_INDEX_URL = "https://mirrors.volces.com/pypi/simple/";
export const OFFICIAL_PIP_INDEX_URL = "https://pypi.org/simple/";
export const DEFAULT_NPM_REGISTRY = "https://registry.npmmirror.com";

export const FALLBACK_LEGACY: PluginVariant = {
  dir: "openclaw-memory-plugin",
  id: "memory-openviking",
  kind: "memory",
  slot: "memory",
  required: ["index.ts", "config.ts", "openclaw.plugin.json", "package.json"],
  optional: ["package-lock.json", ".gitignore"],
  generation: "legacy",
  slotFallback: "none",
};

export const FALLBACK_CURRENT: PluginVariant = {
  dir: "openclaw-plugin",
  id: "openviking",
  kind: "context-engine",
  slot: "contextEngine",
  required: ["index.ts", "config.ts", "package.json"],
  optional: [
    "context-engine.ts",
    "client.ts",
    "process-manager.ts",
    "memory-ranking.ts",
    "text-utils.ts",
    "tool-call-id.ts",
    "session-transcript-repair.ts",
    "openclaw.plugin.json",
    "tsconfig.json",
    "package-lock.json",
    ".gitignore",
  ],
  generation: "current",
  slotFallback: "legacy",
};

export const PLUGIN_VARIANTS: PluginVariant[] = [FALLBACK_LEGACY, FALLBACK_CURRENT];
