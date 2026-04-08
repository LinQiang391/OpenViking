import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export async function tryFetch(url: string, timeout = 15000): Promise<string | null> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (response.ok) return await response.text();
  } catch {}
  return null;
}

export async function testRemoteFile(url: string): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(url, { method: "HEAD", signal: controller.signal });
    clearTimeout(timeoutId);
    return response.ok;
  } catch {}
  return false;
}

export async function downloadFile(
  url: string,
  destPath: string,
  maxRetries = 3,
): Promise<{ ok: boolean; status: number; saw404: boolean; networkError: boolean }> {
  let lastStatus = 0;
  let saw404 = false;
  let networkError = false;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);
      const response = await fetch(url, { signal: controller.signal });
      clearTimeout(timeoutId);
      lastStatus = response.status;
      networkError = false;
      if (response.ok) {
        const buffer = Buffer.from(await response.arrayBuffer());
        if (buffer.length === 0) {
          lastStatus = 0;
        } else {
          await mkdir(dirname(destPath), { recursive: true });
          await writeFile(destPath, buffer);
          return { ok: true, status: lastStatus, saw404: false, networkError: false };
        }
      } else if (response.status === 404) {
        saw404 = true;
        break;
      }
    } catch {
      lastStatus = 0;
      networkError = true;
    }

    if (attempt < maxRetries) {
      await new Promise((resolve) => setTimeout(resolve, 2000 * attempt));
    }
  }

  return { ok: false, status: lastStatus, saw404, networkError };
}
