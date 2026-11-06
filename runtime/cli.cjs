"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const keys = new Set([
  "sdk",
  "cjc",
  "cjdb",
  "cjpm",
  "sysroot",
  "python_framework",
  "cli",
]);
const commands = ["inspectcoder-cj", "inspect-cj"];
const guidance =
  'Install InspectCoder CJ with "uv tool install --python python3.11 inspectcoder-cj", then use "inspectcoder-cj configure --cli /absolute/path/to/inspectcoder-cj" or set INSPECT_CJ to the installed executable.';

function absolute(value) {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    !value.includes("\0") &&
    path.isAbsolute(value)
  );
}

function readConfig(env, home) {
  const explicit = env.INSPECT_CJ_CONFIG !== undefined;
  if (explicit && !absolute(env.INSPECT_CJ_CONFIG))
    throw new Error(
      "Invalid InspectCJ config: INSPECT_CJ_CONFIG must be an absolute file path.",
    );
  if (env.XDG_CONFIG_HOME !== undefined && !absolute(env.XDG_CONFIG_HOME))
    throw new Error(
      "Invalid InspectCJ config: XDG_CONFIG_HOME must be an absolute directory path.",
    );
  const file = explicit
    ? env.INSPECT_CJ_CONFIG
    : path.join(
        env.XDG_CONFIG_HOME ?? path.join(home, ".config"),
        "inspect-cj",
        "config.json",
      );
  let config, raw;
  try {
    // Nonblocking open prevents a mistaken FIFO config from hanging GUI hosts.
    const fd = fs.openSync(
      file,
      fs.constants.O_RDONLY | fs.constants.O_NONBLOCK,
    );
    try {
      const stat = fs.fstatSync(fd);
      if (!stat.isFile() || stat.size > 65536)
        throw new Error("expected a regular JSON file no larger than 64 KiB");
      const buffer = Buffer.alloc(65536 + 1);
      let total = 0;
      while (total < buffer.length) {
        const count = fs.readSync(
          fd,
          buffer,
          total,
          buffer.length - total,
          null,
        );
        if (!count) break;
        total += count;
      }
      if (total > 65536)
        throw new Error("expected a JSON file no larger than 64 KiB");
      raw = buffer.subarray(0, total).toString("utf8");
      config = JSON.parse(raw);
    } finally {
      fs.closeSync(fd);
    }
  } catch (error) {
    if (!explicit && error.code === "ENOENT") return {};
    throw new Error(
      `Cannot read InspectCJ config ${file}: ${error.message}. Fix this config file before launching InspectCJ.`,
    );
  }
  if (!config || Array.isArray(config) || typeof config !== "object")
    throw new Error(
      `Invalid InspectCJ config ${file}: expected a flat JSON object.`,
    );
  // JSON.parse validates syntax first. Tokenizing complete string literals keeps
  // punctuation inside path values literal, and decoded keys catch escaped aliases.
  const tokens = raw.match(/"(?:[^"\\]|\\.)*"|:/g) ?? [];
  const seen = new Set();
  for (let index = 0; index < tokens.length - 1; index++) {
    if (tokens[index + 1] !== ":") continue;
    const key = JSON.parse(tokens[index]);
    if (seen.has(key))
      throw new Error(
        `Invalid InspectCJ config ${file}: duplicate key ${key}.`,
      );
    seen.add(key);
  }
  for (const [key, value] of Object.entries(config)) {
    if (!keys.has(key))
      throw new Error(`Invalid InspectCJ config ${file}: unknown key ${key}.`);
    if (!absolute(value))
      throw new Error(
        `Invalid InspectCJ config ${file}: ${key} must be a nonempty absolute path string.`,
      );
  }
  return config;
}

function isExecutable(file) {
  try {
    if (!fs.statSync(file).isFile()) return false;
    fs.accessSync(file, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function search(names, directories) {
  for (const directory of directories) {
    // A GUI host's project directory must never become an implicit tool source.
    if (!absolute(directory)) continue;
    for (const name of names) {
      const file = path.join(directory, name);
      if (isExecutable(file)) return file;
    }
  }
}

/** Resolve only user/host-owned settings. Never read project configuration. */
function resolveCli(options = {}) {
  const env = options.env ?? process.env;
  const home = options.homeDir ?? env.HOME ?? os.homedir();
  if (!absolute(home))
    throw new Error("InspectCJ requires an absolute user home directory.");
  const config = readConfig(env, home);
  const directories = (env.PATH ?? "").split(path.delimiter);
  const requested = options.executable ?? env.INSPECT_CJ ?? config.cli;
  if (requested !== undefined) {
    if (
      typeof requested !== "string" ||
      !requested.trim() ||
      requested.includes("\0")
    )
      throw new Error(`Invalid InspectCJ executable setting. ${guidance}`);
    if (path.isAbsolute(requested)) {
      if (isExecutable(requested)) return requested;
    } else if (!requested.includes("/") && !requested.includes("\\")) {
      const found = search([requested], directories);
      if (found) return found;
    } else {
      throw new Error(
        `InspectCJ executable paths must be absolute: ${requested}. ${guidance}`,
      );
    }
    throw new Error(
      `InspectCJ executable is missing or not executable: ${requested}. ${guidance}`,
    );
  }
  const onPath = search(commands, directories);
  if (onPath) return onPath;
  const userDirectories = [
    path.join(home, ".local", "bin"),
    path.join(home, "bin"),
  ];
  if ((options.platform ?? process.platform) === "darwin") {
    const python = path.join(home, "Library", "Python");
    try {
      const versions = fs
        .readdirSync(python)
        .filter((name) => /^\d+\.\d+$/.test(name))
        .sort((a, b) => b.localeCompare(a, "en", { numeric: true }));
      userDirectories.push(
        ...versions.map((version) => path.join(python, version, "bin")),
      );
    } catch (error) {
      if (error.code !== "ENOENT")
        throw new Error(
          `Cannot inspect Python user CLI directory ${python}: ${error.message}. ${guidance}`,
        );
    }
  }
  const found = search(commands, userDirectories);
  if (found) return found;
  throw new Error(`Cannot find an installed InspectCJ executable. ${guidance}`);
}

module.exports = { resolveCli };
