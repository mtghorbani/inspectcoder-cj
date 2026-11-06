"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const extensionFile = path.resolve(__dirname, "../extension.js");
const extensionRequire = createRequire(extensionFile);

async function runCommand(root, settings, trusted = true) {
  const commands = new Map();
  const disposable = { dispose() {} };
  const uri = (file) => ({
    scheme: "file",
    fsPath: file,
    toString: () => file,
  });
  const folder = { uri: uri(root) };
  // VS Code supplies the UI APIs. The extension, resolver, filesystem and CLI
  // execution are real so this catches discovery and trust-boundary regressions.
  const vscode = {
    workspace: {
      isTrusted: trusted,
      textDocuments: [],
      getWorkspaceFolder: () => folder,
      getConfiguration: () => ({
        get: (key, fallback) => settings[key] ?? fallback,
      }),
    },
    env: {},
    Uri: { file: uri },
    languages: {
      createDiagnosticCollection: () => ({
        ...disposable,
        delete() {},
        set() {},
      }),
    },
    window: {
      createOutputChannel: () => ({
        ...disposable,
        show() {},
        appendLine() {},
      }),
      withProgress: async (_, action) =>
        action({ report() {} }, { onCancellationRequested: () => disposable }),
      showErrorMessage() {},
    },
    ProgressLocation: { Notification: 1 },
    DiagnosticSeverity: { Error: 0 },
    Diagnostic: class {},
    Range: class {},
    commands: {
      registerCommand: (name, action) => {
        commands.set(name, action);
        return disposable;
      },
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(
    fs.readFileSync(extensionFile, "utf8"),
    {
      require: (name) => (name === "vscode" ? vscode : extensionRequire(name)),
      module,
      process,
      AbortController,
      setInterval,
      clearInterval,
    },
    { filename: extensionFile },
  );
  module.exports.activate({
    subscriptions: [],
    globalStorageUri: uri(path.join(root, "storage")),
  });
  return commands.get("inspectCJ.repairFile")(uri(path.join(root, "main.cj")), {
    expectLine: ["42"],
  });
}

for (const source of ["PATH", "config", "user bin"]) {
  test(`default VS Code setting discovers CLI through ${source} with sparse GUI PATH`, async (t) => {
    const root = fs.realpathSync(
      fs.mkdtempSync(path.join(os.tmpdir(), "inspect cj vscode ")),
    );
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    fs.writeFileSync(path.join(root, "main.cj"), "main() { 42 }");
    const bin = path.join(
      root,
      source === "user bin" ? ".local/bin" : "bin with spaces",
    );
    fs.mkdirSync(bin, { recursive: true });
    const cli = path.join(bin, "inspectcoder-cj");
    fs.writeFileSync(
      cli,
      `#!${process.execPath}\nconsole.log(JSON.stringify({status:"needs_input",cli:process.argv[1],root:process.env.INSPECT_CJ_WORKSPACE_ROOT}));`,
      { mode: 0o755 },
    );
    const environment = { HOME: root, PATH: source === "PATH" ? bin : "" };
    if (source === "config") {
      const file = path.join(root, "config with spaces.json");
      fs.writeFileSync(file, JSON.stringify({ cli }));
      environment.INSPECT_CJ_CONFIG = file;
    }
    const original = { ...process.env };
    for (const key of ["INSPECT_CJ", "INSPECT_CJ_CONFIG", "XDG_CONFIG_HOME"])
      delete process.env[key];
    try {
      Object.assign(process.env, { HOME: root, PATH: "" });
      const result = await runCommand(root, { environment });
      assert.equal(result.code, 0, JSON.stringify(result));
      assert.equal(result.json.cli, cli);
      assert.equal(result.json.root, root);
      assert.equal(process.env.PATH, "");
      assert.equal(process.env.INSPECT_CJ, undefined);
      assert.equal(process.env.INSPECT_CJ_CONFIG, undefined);
    } finally {
      for (const key of Object.keys(process.env))
        if (!(key in original)) delete process.env[key];
      Object.assign(process.env, original);
    }
  });
}

test("untrusted VS Code workspace cannot launch a configured executable", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "inspect cj trust "));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const receipt = path.join(root, "launched");
  const cli = path.join(root, "inspectcoder-cj");
  fs.writeFileSync(cli, `#!/bin/sh\ntouch '${receipt}'\n`, { mode: 0o755 });
  fs.writeFileSync(path.join(root, "main.cj"), "main() { 42 }");
  const result = await runCommand(root, { executable: cli }, false);
  assert.equal(result.code, 2);
  assert.match(result.json.reason, /trust/i);
  assert.equal(fs.existsSync(receipt), false);
});
