import { execSync, spawn, } from "node:child_process";
export function launchProcess(command, args, options) {
    return spawn(command, args, options);
}
export function runSync(command, options) {
    return execSync(command, options);
}
const _env = globalThis["process"];
export function sysEnv() {
    return _env.env;
}
export function getEnv(key) {
    return _env.env[key];
}
