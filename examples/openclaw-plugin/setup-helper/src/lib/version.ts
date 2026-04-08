export function versionGte(v1: string, v2: string): boolean {
  const parseVersion = (v: string) => {
    const cleaned = v.replace(/^v/, "").replace(/-.*$/, "");
    const parts = cleaned.split(".").map((p) => Number.parseInt(p, 10) || 0);
    while (parts.length < 3) parts.push(0);
    return parts;
  };
  const [a1, a2, a3] = parseVersion(v1);
  const [b1, b2, b3] = parseVersion(v2);
  if (a1 !== b1) return a1 > b1;
  if (a2 !== b2) return a2 > b2;
  return a3 >= b3;
}

export function isSemverLike(value: string): boolean {
  return /^v?\d+(\.\d+){1,2}$/.test(value);
}

export function compareSemverDesc(a: string, b: string): number {
  if (versionGte(a, b) && versionGte(b, a)) return 0;
  return versionGte(a, b) ? -1 : 1;
}

export function pickLatestPluginTag(tagNames: string[]): string {
  const normalized = tagNames.map((tag) => String(tag ?? "").trim()).filter(Boolean);
  const semverTags = normalized.filter((tag) => isSemverLike(tag)).sort(compareSemverDesc);
  if (semverTags.length > 0) return semverTags[0];
  return normalized[0] || "";
}

export function parseGitLsRemoteTags(output: string): string[] {
  return String(output ?? "")
    .split(/\r?\n/)
    .map((line) => {
      const match = line.match(/refs\/tags\/(.+)$/);
      return match?.[1]?.trim() || "";
    })
    .filter(Boolean);
}

export function normalizeCombinedVersion(version: string): {
  pluginVersion: string;
  openvikingVersion: string;
} {
  const value = (version || "").trim();
  if (!/^(v)?\d+(\.\d+){1,2}$/.test(value)) {
    console.error("--version requires a semantic version like 0.2.9 or v0.2.9");
    process.exit(1);
  }
  const openvikingVersion = value.startsWith("v") ? value.slice(1) : value;
  return {
    pluginVersion: `v${openvikingVersion}`,
    openvikingVersion,
  };
}

export function deriveOpenvikingVersionFromPluginVersion(version: string): string {
  const value = (version || "").trim();
  if (!isSemverLike(value)) return "";
  return value.startsWith("v") ? value.slice(1) : value;
}

export function sortSemverTagsDesc(tags: string[]): string[] {
  return tags.filter((t) => isSemverLike(t)).sort(compareSemverDesc);
}
