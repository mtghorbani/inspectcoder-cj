"use strict";
const { spawn } = require("node:child_process");
const path = require("node:path");

const strings = {
  task: "--task",
  driver: "--driver",
  stdinFile: "--stdin-file",
  expectStdout: "--expect-stdout",
  expectStderr: "--expect-stderr",
  editable: "--editable",
  case: "--case",
};
const numbers = {
  expectExit: "--expect-exit",
  runTimeout: "--run-timeout",
  maxAttempts: "--max-attempts",
  maxSteps: "--max-steps",
};
function buildArgs(kind, target, request = {}) {
  if (!request || typeof request !== "object" || Array.isArray(request))
    throw new Error("Request must be a JSON object");
  const allowed = new Set([
    ...Object.keys(strings),
    ...Object.keys(numbers),
    "args",
    "expectLine",
    "compileOnly",
  ]);
  for (const key of Object.keys(request))
    if (!allowed.has(key)) throw new Error(`Unknown request field: ${key}`);
  if (request.expectLine !== undefined && request.expectStdout !== undefined)
    throw new Error("Use expectLine or expectStdout");
  if (
    kind === "package" &&
    [
      "driver",
      "stdinFile",
      "expectStdout",
      "expectStderr",
      "expectExit",
      "expectLine",
      "args",
      "compileOnly",
    ].some((k) => request[k] !== undefined)
  )
    throw new Error("Native assertions cannot be overridden");
  const result = ["repair", target, "--non-interactive"];
  for (const [key, flag] of Object.entries(strings))
    if (request[key] !== undefined) {
      if (typeof request[key] !== "string")
        throw new Error(`${key} must be a string`);
      if ((key === "task" || key === "stdinFile") && request[key] === "-")
        throw new Error("Editor stdin requires a file or task text");
      result.push(flag, request[key]);
    }
  if (request.expectLine !== undefined) {
    if (
      !Array.isArray(request.expectLine) ||
      !request.expectLine.every((s) => typeof s === "string")
    )
      throw new Error("expectLine must be an array of strings");
    for (const line of request.expectLine) result.push("--expect-line", line);
  }
  for (const [key, flag] of Object.entries(numbers))
    if (request[key] !== undefined) {
      const value = request[key];
      if (
        !Number.isFinite(value) ||
        (key === "expectExit"
          ? !Number.isInteger(value)
          : value <= 0 || (key !== "runTimeout" && !Number.isInteger(value)))
      )
        throw new Error(`Invalid ${key}`);
      result.push(flag, String(value));
    }
  if (request.compileOnly !== undefined) {
    if (typeof request.compileOnly !== "boolean")
      throw new Error("compileOnly must be boolean");
    if (request.compileOnly) result.push("--compile-only");
  }
  if (request.args !== undefined) {
    if (
      !Array.isArray(request.args) ||
      !request.args.every((s) => typeof s === "string")
    )
      throw new Error("args must be an array of strings");
    result.push("--", ...request.args);
  }
  return result;
}
function checkContext(c) {
  if (!c.trusted) throw new Error("InspectCJ requires Workspace Trust");
  if (c.remote || c.scheme !== "file")
    throw new Error("InspectCJ currently supports local file workspaces only");
  if (!["darwin", "linux"].includes(c.platform))
    throw new Error("InspectCJ requires a local POSIX host");
  if (c.dirty) throw new Error("Save the source before running InspectCJ");
  const relative = path.relative(c.workspace, c.target);
  if (
    relative.startsWith(".." + path.sep) ||
    relative === ".." ||
    path.isAbsolute(relative)
  )
    throw new Error("Target must be inside the selected workspace");
}
function runCli(executable, args, options) {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted)
      return reject(new Error("Cancelled before launch"));
    const child = spawn(executable, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      shell: false,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "",
      stderr = "",
      timer,
      failure;
    const kill = (signal) => {
      try {
        process.kill(-child.pid, signal);
      } catch (e) {
        if (e.code !== "ESRCH") failure ||= e;
      }
    };
    const cancel = () => {
      kill("SIGINT");
      timer ||= setTimeout(() => kill("SIGKILL"), 5000);
    };
    options.signal?.addEventListener("abort", cancel, { once: true });
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    for (const [stream, name] of [
      [child.stdout, "stdout"],
      [child.stderr, "stderr"],
    ])
      stream.on("data", (data) => {
        if (name === "stdout") stdout += data;
        else stderr += data;
        if (stdout.length + stderr.length > 4 * 1024 * 1024 && !failure) {
          failure = new Error("CLI output exceeds 4MiB");
          kill("SIGKILL");
        }
      });
    child.on("error", (error) => {
      failure = error;
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", cancel);
      if (failure) return reject(failure);
      try {
        const json = JSON.parse(stdout);
        if (!json || typeof json.status !== "string")
          throw new Error("Missing CLI status");
        resolve({ code, json, stderr });
      } catch (e) {
        reject(
          new Error(
            `Invalid CLI JSON (exit ${code}): ${e.message}; ${stderr.slice(-2000)}`,
          ),
        );
      }
    });
  });
}
module.exports = { buildArgs, checkContext, runCli };
