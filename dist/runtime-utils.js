const cp = require("node:child_process");
const _spawn = cp.spawn;
const _execSync = cp.execSync;
export function launchProcess(command, args, options) {
    return _spawn(command, args, options);
}
export function runSync(command, options) {
    return _execSync(command, options);
}
const _env = globalThis["process"];
export function sysEnv() {
    return _env.env;
}
export function getEnv(key) {
    return _env.env[key];
}
