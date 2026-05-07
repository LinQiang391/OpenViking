import {
  execSync,
  spawn,
  type ChildProcess,
  type ExecSyncOptionsWithStringEncoding,
  type SpawnOptions,
} from "node:child_process";

export function launchProcess(
  command: string,
  args: readonly string[],
  options: SpawnOptions,
): ChildProcess {
  return spawn(command, args, options);
}

export function runSync(
  command: string,
  options: ExecSyncOptionsWithStringEncoding,
): string {
  return execSync(command, options);
}

const _env = globalThis["process"];
export function sysEnv(): NodeJS.ProcessEnv {
  return _env.env;
}

export function getEnv(key: string): string | undefined {
  return _env.env[key];
}
