import { defineCommand, runMain } from "citty";
import { join } from "node:path";
import type { InstallContext } from "./types.js";
import {
  DEFAULT_SERVER_PORT,
  DEFAULT_NPM_REGISTRY,
  DEFAULT_PIP_INDEX_URL,
} from "./types.js";
import { detectPlatform } from "./lib/platform.js";
import { normalizeCombinedVersion, deriveOpenvikingVersionFromPluginVersion } from "./lib/version.js";
import { runInstall } from "./commands/install.js";

const platform = detectPlatform();

const main = defineCommand({
  meta: {
    name: "ov-install",
    version: "0.3.0-beta.1",
    description: "OpenClaw + OpenViking cross-platform installer",
  },
  args: {
    "github-repo": {
      type: "string",
      description: "GitHub repository (default: volcengine/OpenViking)",
      default: process.env.REPO || "volcengine/OpenViking",
    },
    version: {
      type: "string",
      description: "Shorthand for --plugin-version=vV and --openviking-version=V",
      default: "",
    },
    "plugin-version": {
      type: "string",
      description: "Plugin version (Git tag, e.g. v0.2.9, default: latest tag)",
      default: process.env.PLUGIN_VERSION || process.env.BRANCH || "",
    },
    "openviking-version": {
      type: "string",
      description: "OpenViking PyPI version (e.g. 0.2.9, default: match plugin release version)",
      default: process.env.OPENVIKING_VERSION || "",
    },
    workdir: {
      type: "string",
      description: "OpenClaw config directory (default: ~/.openclaw)",
      default: "",
    },
    "current-version": {
      type: "boolean",
      description: "Print installed plugin/OpenViking versions and exit",
      default: false,
    },
    repo: {
      type: "string",
      description: "Use local OpenViking repo at PATH (pip -e + local plugin)",
      default: process.env.OPENVIKING_REPO || "",
    },
    update: {
      type: "boolean",
      description: "Upgrade only the plugin (alias: --upgrade-plugin, --upgrade)",
      default: false,
    },
    "upgrade-plugin": {
      type: "boolean",
      description: "Upgrade only the plugin",
      default: false,
    },
    upgrade: {
      type: "boolean",
      description: "Upgrade only the plugin",
      default: false,
    },
    rollback: {
      type: "boolean",
      description: "Roll back the last plugin upgrade",
      default: false,
    },
    "rollback-last-upgrade": {
      type: "boolean",
      description: "Roll back the last plugin upgrade",
      default: false,
    },
    yes: {
      type: "boolean",
      alias: ["y"],
      description: "Non-interactive (use defaults)",
      default: process.env.OPENVIKING_INSTALL_YES === "1",
    },
    zh: {
      type: "boolean",
      description: "Chinese prompts",
      default: false,
    },
  },
  run({ args }) {
    const upgradePluginOnly = args.update || args["upgrade-plugin"] || args.upgrade;
    const rollbackLastUpgrade = args.rollback || args["rollback-last-upgrade"];

    if (upgradePluginOnly && rollbackLastUpgrade) {
      console.error("--update/--upgrade-plugin and --rollback cannot be used together");
      process.exit(1);
    }

    let pluginVersion = args["plugin-version"];
    let openvikingVersion = args["openviking-version"];
    let pluginVersionExplicit = Boolean(pluginVersion);

    if (args.version) {
      if (pluginVersion || openvikingVersion) {
        console.error(
          "--version cannot be used together with --plugin-version or --openviking-version",
        );
        process.exit(1);
      }
      const normalized = normalizeCombinedVersion(args.version);
      pluginVersion = normalized.pluginVersion;
      openvikingVersion = normalized.openvikingVersion;
      pluginVersionExplicit = true;
    }

    if ((upgradePluginOnly || rollbackLastUpgrade) && openvikingVersion) {
      console.error(
        "Plugin-only upgrade/rollback does not support --openviking-version or --version.",
      );
      process.exit(1);
    }

    // Sync openviking version from plugin version if not explicitly set
    if (!openvikingVersion && pluginVersion) {
      const derived = deriveOpenvikingVersionFromPluginVersion(pluginVersion);
      if (derived) openvikingVersion = derived;
    }

    const defaultOpenclawDir = join(platform.home, ".openclaw");
    const openclawDir = args.workdir || defaultOpenclawDir;
    const openvikingDir = join(platform.home, ".openviking");

    const ctx: InstallContext = {
      repo: args["github-repo"],
      pluginVersion,
      pluginVersionExplicit,
      openvikingVersion,
      openvikingRepo: args.repo,
      openclawDir,
      defaultOpenclawDir,
      openvikingDir,
      interactive: !args.yes,
      langZh: args.zh,
      workdirExplicit: Boolean(args.workdir),

      platform,
      pluginConfig: null,
      pluginDest: "",
      mode: "local",
      runtimeConfig: null,

      pythonPath: "",
      envFiles: null,

      upgradePluginOnly,
      rollbackLastUpgrade,
      showCurrentVersion: args["current-version"],

      upgradeRuntimeConfig: null,
      installedUpgradeState: null,
      upgradeAudit: null,

      remoteBaseUrl: "http://127.0.0.1:1933",
      remoteApiKey: "",
      remoteAgentId: "",
      selectedServerPort: DEFAULT_SERVER_PORT,

      npmRegistry: process.env.NPM_REGISTRY || DEFAULT_NPM_REGISTRY,
      pipIndexUrl: process.env.PIP_INDEX_URL || DEFAULT_PIP_INDEX_URL,
    };

    return runInstall(ctx);
  },
});

runMain(main);
