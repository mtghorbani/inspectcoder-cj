"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const resolverPath = path.resolve(__dirname, "../cli.cjs");

function fixture(t) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "inspect cj resolver "));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const env = { HOME: home, PATH: "" };
  const executable = (name) => {
    const file = path.join(home, name);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "#!/bin/sh\nprintf '%s' resolved\n", {
      mode: 0o755,
    });
    return file;
  };
  const config = (
    value,
    file = path.join(home, ".config/inspect-cj/config.json"),
  ) => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(
      file,
      typeof value === "string" ? value : JSON.stringify(value),
    );
    return file;
  };
  // A separate process mirrors a GUI host, without inheriting author CLI settings.
  const resolve = (options = {}) => {
    const child = spawnSync(
      process.execPath,
      [
        "-e",
        `try { const {resolveCli} = require(${JSON.stringify(resolverPath)}); process.stdout.write(JSON.stringify({path:resolveCli(${JSON.stringify(options)})})); } catch(e) { process.stdout.write(JSON.stringify({error:e.message})); process.exitCode=1; }`,
      ],
      { env, encoding: "utf8" },
    );
    const result = JSON.parse(child.stdout);
    if (child.status !== 0) throw new Error(result.error);
    return result.path;
  };
  return { home, env, executable, config, resolve };
}

test("a clean host discovers an executable on PATH and preserves spaces literally", (t) => {
  const f = fixture(t);
  const cli = f.executable("bin with spaces/inspect-cj");
  f.env.PATH = path.dirname(cli);
  assert.equal(f.resolve(), cli);
  assert.equal(
    spawnSync(f.resolve(), [], { encoding: "utf8", shell: false }).stdout,
    "resolved",
  );
});

test("GUI sparse PATH discovers the normal user bin without exports", (t) => {
  const f = fixture(t);
  const cli = f.executable(".local/bin/inspect-cj");
  assert.equal(f.resolve(), cli);
});

test("a macOS GUI host discovers Python user installations in newest version order", (t) => {
  const f = fixture(t);
  f.executable("Library/Python/3.9/bin/inspect-cj");
  const cli = f.executable("Library/Python/3.13/bin/inspect-cj");
  assert.equal(f.resolve({ platform: "darwin" }), cli);
});

test("user config and XDG config select a literal absolute CLI path", (t) => {
  const f = fixture(t);
  const cli = f.executable("tool directory/inspect-cj $(literal)");
  f.config({ cli, sdk: path.join(f.home, "sdk") });
  assert.equal(f.resolve(), cli);
  f.env.XDG_CONFIG_HOME = path.join(f.home, "xdg settings");
  const other = f.executable("other/inspect-cj");
  f.config(
    { cli: other },
    path.join(f.env.XDG_CONFIG_HOME, "inspect-cj/config.json"),
  );
  assert.equal(f.resolve(), other);
});

test("explicit client, legacy override, config, PATH and user bin retain precedence", (t) => {
  const f = fixture(t);
  const explicit = f.executable("client/inspect-cj");
  const legacy = f.executable("legacy/inspect-cj");
  const configured = f.executable("configured/inspect-cj");
  const onPath = f.executable("path/inspect-cj");
  f.executable(".local/bin/inspect-cj");
  f.env.PATH = path.dirname(onPath);
  f.config({ cli: configured });
  f.env.INSPECT_CJ = legacy;
  assert.equal(f.resolve({ executable: explicit }), explicit);
  assert.equal(f.resolve(), legacy);
  delete f.env.INSPECT_CJ;
  assert.equal(f.resolve(), configured);
  f.config({});
  assert.equal(f.resolve(), onPath);
});

test("explicit config path is honored, and a missing explicit file fails", (t) => {
  const f = fixture(t);
  const cli = f.executable("custom/inspect-cj");
  f.env.INSPECT_CJ_CONFIG = f.config(
    { cli },
    path.join(f.home, "custom config.json"),
  );
  assert.equal(f.resolve(), cli);
  f.env.INSPECT_CJ_CONFIG = path.join(f.home, "missing.json");
  assert.throws(() => f.resolve(), /config.*missing\.json/i);
});

test("invalid configuration cannot silently fall through to an installed executable", (t) => {
  const f = fixture(t);
  const cli = f.executable(".local/bin/inspect-cj");
  for (const value of [
    "{",
    "null",
    "[]",
    { token: "secret" },
    { cli: "relative" },
    { sdk: "" },
    { cjc: 3 },
    { cli, cjdb: { path: "/tool" } },
  ]) {
    f.config(value);
    assert.throws(() => f.resolve({ executable: cli }), /config/i);
  }
});

test("configured missing and non-executable tools produce actionable errors without fallback", (t) => {
  const f = fixture(t);
  f.executable(".local/bin/inspect-cj");
  const missing = path.join(f.home, "missing");
  assert.throws(
    () => f.resolve({ executable: missing }),
    /INSPECT_CJ|configure/i,
  );
  const file = f.executable("not executable");
  fs.chmodSync(file, 0o644);
  f.config({ cli: file });
  assert.throws(() => f.resolve(), /executable/i);
  f.config({ cli: f.home });
  assert.throws(() => f.resolve(), /executable/i);
});

test("missing tools explain installation and persistent CLI configuration", (t) => {
  const f = fixture(t);
  assert.throws(
    () => f.resolve(),
    /install.*inspect-cj.*configure|configure.*cli/i,
  );
});

test("workspace-relative PATH and explicit paths never select project executables", (t) => {
  const f = fixture(t);
  f.env.PATH = ".:relative:";
  assert.throws(() => f.resolve(), /install|configure/i);
  assert.throws(() => f.resolve({ executable: "./inspect-cj" }), /absolute/i);
});

test("legacy explicit command names resolve to an absolute PATH executable", (t) => {
  const f = fixture(t);
  const cli = f.executable("custom/my-inspector");
  f.env.PATH = path.dirname(cli);
  assert.equal(f.resolve({ executable: "my-inspector" }), cli);
});

test("canonical CLI command is preferred while legacy installs remain usable", (t) => {
  const f = fixture(t);
  const legacy = f.executable("bin/inspect-cj");
  const canonical = f.executable("bin/inspectcoder-cj");
  f.env.PATH = path.dirname(legacy);
  assert.equal(f.resolve(), canonical);
});

test("duplicate config keys including escaped aliases are rejected", (t) => {
  const f = fixture(t);
  const cli = f.executable(".local/bin/inspectcoder-cj");
  for (const raw of [
    `{"cli":${JSON.stringify(cli)},"cli":${JSON.stringify(cli)}}`,
    `{"cli":${JSON.stringify(cli)},"cl\\u0069":${JSON.stringify(cli)}}`,
  ]) {
    f.config(raw);
    assert.throws(() => f.resolve(), /duplicate.*cli/i);
  }
});

test("quoted punctuation in a config path is literal, not a duplicate key", (t) => {
  const f = fixture(t);
  const cli = f.executable('directory "cli": with punctuation/inspectcoder-cj');
  f.config({ cli });
  assert.equal(f.resolve(), cli);
});

test("FIFO user config is rejected promptly in an isolated host process", (t) => {
  const f = fixture(t);
  const file = path.join(f.home, "config fifo");
  const created = spawnSync("/usr/bin/mkfifo", [file], { encoding: "utf8" });
  assert.equal(created.status, 0, created.stderr);
  const child = spawnSync(
    process.execPath,
    [
      "-e",
      `try { require(${JSON.stringify(resolverPath)}).resolveCli(); process.exitCode = 2; } catch(e) { process.stderr.write(e.message); process.exitCode = 1; }`,
    ],
    {
      env: { ...f.env, INSPECT_CJ_CONFIG: file },
      encoding: "utf8",
      timeout: 2000,
    },
  );
  assert.equal(child.error, undefined, "config FIFO must not block the host");
  assert.equal(child.status, 1);
  assert.match(child.stderr, /config.*regular.*file/i);
});

test("config reads remain bounded when a regular file grows after descriptor validation", (t) => {
  const f = fixture(t);
  const file = f.config({ sdk: "/sdk" });
  const child = spawnSync(
    process.execPath,
    [
      "-e",
      `const fs = require("node:fs"); const stat = fs.fstatSync; fs.fstatSync = function(fd) { const result = stat(fd); fs.appendFileSync(${JSON.stringify(file)}, " ".repeat(65536)); return result; }; try { require(${JSON.stringify(resolverPath)}).resolveCli(); process.exitCode = 2; } catch(e) { process.stderr.write(e.message); process.exitCode = 1; }`,
    ],
    { env: f.env, encoding: "utf8", timeout: 2000 },
  );
  assert.equal(child.error, undefined);
  assert.equal(child.status, 1);
  assert.match(child.stderr, /config.*64 KiB/i);
});

test("config accepts exactly 64 KiB and rejects the next byte", (t) => {
  const f = fixture(t);
  const cli = f.executable(".local/bin/inspectcoder-cj");
  const exact = '{"sdk":"/sdk"}'.padEnd(65536, " ");
  f.config(exact);
  assert.equal(f.resolve(), cli);
  f.config(exact + " ");
  assert.throws(() => f.resolve(), /config.*64 KiB/i);
});
