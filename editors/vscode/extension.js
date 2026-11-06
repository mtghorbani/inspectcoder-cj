"use strict";
const vscode = require("vscode");
const fs = require("node:fs/promises");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { buildArgs, checkContext, runCli } = require("./core");
const { resolveCli } = require("./runtime/cli.cjs");

function activate(context) {
  const output = vscode.window.createOutputChannel("InspectCJ");
  const diagnostics = vscode.languages.createDiagnosticCollection("inspect-cj");
  let active, lastRun;
  context.subscriptions.push(output, diagnostics, {
    dispose() {
      active?.abort();
    },
  });
  function guard(uri) {
    if (!uri) throw new Error("Open a saved Cangjie source in a workspace");
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) throw new Error("Open the target folder as a workspace");
    checkContext({
      trusted: vscode.workspace.isTrusted,
      remote: !!vscode.env.remoteName,
      platform: process.platform,
      scheme: uri.scheme,
      target: uri.fsPath,
      workspace: folder.uri.fsPath,
      dirty: vscode.workspace.textDocuments.some(
        (d) =>
          d.uri.toString() === uri.toString() && (d.isDirty || d.isUntitled),
      ),
    });
    return folder;
  }
  async function canonical(uri) {
    const folder = guard(uri);
    const root = await fs.realpath(folder.uri.fsPath);
    const target = await fs.realpath(uri.fsPath);
    checkContext({
      trusted: true,
      remote: false,
      platform: process.platform,
      scheme: "file",
      target,
      workspace: root,
      dirty: false,
    });
    // Prevent a CLI canonical target from bypassing open-document stale guards.
    if (target !== uri.fsPath)
      throw new Error("Open the canonical source path instead of a symlink");
    return folder;
  }
  function config(uri) {
    const settings = vscode.workspace.getConfiguration("inspectCJ", uri);
    const executable = settings.get("executable", "inspect-cj");
    const environment = settings.get("environment", {});
    if (
      typeof executable !== "string" ||
      !executable ||
      !environment ||
      Object.values(environment).some((v) => typeof v !== "string")
    )
      throw new Error("Invalid InspectCJ executable/environment configuration");
    return {
      executable,
      env: { ...process.env, ...environment },
      transport: settings.get("transport", "codex"),
      model: settings.get("model", ""),
      script: settings.get("script", ""),
      timeout: settings.get("runTimeout", 600),
      requestFile: settings.get("requestFile", ".inspect-cj/request.json"),
    };
  }
  async function invoke(uri, args, run) {
    const folder = await canonical(uri);
    if (active) throw new Error("An InspectCJ operation is already running");
    const settings = config(uri);
    settings.executable = resolveCli({
      executable: ["inspect-cj", "inspectcoder-cj"].includes(
        settings.executable,
      )
        ? undefined
        : settings.executable,
      env: settings.env,
    });
    active = new AbortController();
    const signal = active.signal;
    output.show(true);
    output.appendLine(
      JSON.stringify({ executable: settings.executable, args }),
    );
    let timer,
      seen = 0,
      reading = false;
    if (run)
      timer = setInterval(async () => {
        if (reading) return;
        reading = true;
        try {
          const lines = (
            await fs.readFile(path.join(run, "events.jsonl"), "utf8")
          )
            .split("\n")
            .filter(Boolean);
          for (const line of lines.slice(seen)) {
            const event = JSON.parse(line);
            output.appendLine(
              `Progress: ${event.type || "event"} ${event.role || ""}`,
            );
          }
          seen = lines.length;
        } catch {
          /* The CLI creates events after its first build; incomplete lines retry. */
        } finally {
          reading = false;
        }
      }, 500);
    try {
      return await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "InspectCJ",
          cancellable: true,
        },
        async (progress, token) => {
          const subscription = token.onCancellationRequested(() =>
            active?.abort(),
          );
          progress.report({
            message: "Running native CLI; details in InspectCJ output",
          });
          try {
            const result = await runCli(settings.executable, args, {
              cwd: folder.uri.fsPath,
              env: {
                ...settings.env,
                INSPECT_CJ_WORKSPACE_ROOT: await fs.realpath(folder.uri.fsPath),
              },
              signal,
            });
            output.appendLine(JSON.stringify(result.json, null, 2));
            if (result.stderr) output.appendLine(result.stderr);
            return result;
          } finally {
            subscription.dispose();
          }
        },
      );
    } finally {
      clearInterval(timer);
      active = undefined;
    }
  }
  async function findPackage(uri, optional = false) {
    const folder = await canonical(uri);
    let directory = (await fs.stat(uri.fsPath)).isDirectory()
      ? uri.fsPath
      : path.dirname(uri.fsPath);
    while (true) {
      if (await fs.stat(path.join(directory, "cjpm.toml")).catch(() => null))
        return vscode.Uri.file(directory);
      if (directory === folder.uri.fsPath) break;
      const parent = path.dirname(directory);
      if (parent === directory) break;
      directory = parent;
    }
    if (optional) return undefined;
    throw new Error("No cjpm.toml found inside this workspace");
  }
  async function fileRequest(uri) {
    const folder = guard(uri),
      name = config(uri).requestFile;
    const requestPath = path.resolve(folder.uri.fsPath, name);
    checkContext({
      trusted: true,
      remote: false,
      platform: process.platform,
      scheme: "file",
      target: requestPath,
      workspace: folder.uri.fsPath,
      dirty: false,
    });
    try {
      await canonical(vscode.Uri.file(requestPath));
      return JSON.parse(await fs.readFile(requestPath, "utf8"));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const input = await vscode.window.showInputBox({
      title: "InspectCJ repair inputs",
      prompt:
        "JSON with independent expectations, optional task, args, stdinFile or driver",
      value:
        '{"task":"Describe intended behavior","expectLine":["expected output"],"args":[]}',
      ignoreFocusOut: true,
    });
    return input === undefined ? undefined : JSON.parse(input);
  }
  function diagnose(uri, result) {
    diagnostics.delete(uri);
    if (
      result.json.status === "success" ||
      result.json.status === "compile_only"
    )
      return;
    const message = [
      result.json.status,
      result.json.reason || "Native test failure",
      result.json.missing?.length
        ? `Missing: ${result.json.missing.join(", ")}`
        : "",
      result.json.choices?.length
        ? `Choices: ${result.json.choices.join(", ")}`
        : "",
    ]
      .filter(Boolean)
      .join("\n");
    const diagnostic = new vscode.Diagnostic(
      new vscode.Range(0, 0, 0, 1),
      message,
      vscode.DiagnosticSeverity.Error,
    );
    diagnostic.source = "InspectCJ";
    diagnostic.code = result.json.status;
    diagnostics.set(uri, [diagnostic]);
  }
  async function review(run = lastRun) {
    if (!run) throw new Error("No candidate in this session");
    const receipt = JSON.parse(
      await fs.readFile(path.join(run, "candidate.json"), "utf8"),
    );
    await canonical(vscode.Uri.file(receipt.target));
    await vscode.commands.executeCommand(
      "vscode.diff",
      vscode.Uri.file(path.join(run, "versions", "0.cj")),
      vscode.Uri.file(path.join(run, "repaired.cj")),
      "InspectCJ: Original / Candidate",
      { preview: true },
    );
    return run;
  }
  async function repair(kind, resource, request) {
    const uri = resource || vscode.window.activeTextEditor?.document.uri;
    await canonical(uri);
    const nativeSource = kind === "file" && !!(await findPackage(uri, true));
    const target = kind === "file" ? uri : await findPackage(uri);
    if (kind === "file" && path.extname(uri.fsPath) !== ".cj")
      throw new Error("Current file must be a saved .cj source");
    let inputs =
      request === undefined
        ? kind === "file" && !nativeSource
          ? await fileRequest(uri)
          : {}
        : request;
    if (inputs === undefined) return;
    inputs = { runTimeout: config(uri).timeout, ...inputs };
    if (kind === "selected" && !inputs.case) {
      const discovered = await invoke(target, ["test", target.fsPath]);
      if (!Array.isArray(discovered.json.tests)) {
        diagnose(uri, discovered);
        return discovered;
      }
      const failures = discovered.json.tests.filter(
        (c) => !c.passed && !c.skipped,
      );
      const selected = await vscode.window.showQuickPick(
        failures.map((c) => ({
          label: c.launch
            ? `${c.launch.package}: ${c.launch.suite}.${c.launch.case}`
            : c.name,
          description: c.name,
          case: c.name,
        })),
        {
          title: "Select a failing native test",
          placeHolder: failures.length
            ? "Exact opaque native case"
            : "No failing native cases discovered",
        },
      );
      if (!selected) return;
      inputs.case = selected.case;
    }
    const args = buildArgs(
      kind === "file" && !nativeSource ? "file" : "package",
      target.fsPath,
      inputs,
    );
    const runs = path.join(
      (context.storageUri || context.globalStorageUri).fsPath,
      "runs",
    );
    await fs.mkdir(runs, { recursive: true, mode: 0o700 });
    const run = path.join(runs, randomUUID());
    const settings = config(uri);
    const transportArgs = ["--transport", settings.transport];
    if (settings.model) transportArgs.push("--model", settings.model);
    if (settings.script) transportArgs.push("--script", settings.script);
    const separator = args.indexOf("--");
    args.splice(
      separator < 0 ? args.length : separator,
      0,
      "--output",
      run,
      ...transportArgs,
    );
    const result = await invoke(uri, args, run);
    diagnose(uri, result);
    if (["success", "compile_only"].includes(result.json.status)) {
      lastRun = result.json.output;
      await review(lastRun);
      vscode.window.showInformationMessage(
        result.json.status === "success"
          ? "InspectCJ candidate passed the declared suite. Review the diff, then run Apply Candidate."
          : "InspectCJ candidate compiled. Semantic behavior was not validated.",
      );
    } else if (
      result.json.status === "needs_input" &&
      result.json.missing?.includes("editable") &&
      request === undefined
    ) {
      const editable = await vscode.window.showQuickPick(
        result.json.choices || [],
        { title: "Select one editable implementation source" },
      );
      if (editable) return repair(kind, resource, { ...inputs, editable });
    }
    return result;
  }
  async function apply(run = lastRun) {
    if (!run) throw new Error("No candidate in this session");
    const change = JSON.parse(
      await fs.readFile(path.join(run, "candidate.json"), "utf8"),
    );
    const uri = vscode.Uri.file(change.target);
    await canonical(uri);
    const result = await invoke(uri, ["apply", run]);
    diagnose(uri, result);
    if (result.code === 0)
      vscode.window.showInformationMessage(
        "InspectCJ candidate applied. Rerun your acceptance suite.",
      );
    return result;
  }
  const commands = {
    repairFile: (uri, request) => repair("file", uri, request),
    repairPackage: (uri, request) => repair("package", uri, request),
    repairSelectedTest: (uri, request) => repair("selected", uri, request),
    reviewCandidate: review,
    applyCandidate: apply,
    cancel: () => active?.abort(),
    configureInputs: async () => {
      const document = await vscode.workspace.openTextDocument({
        language: "json",
        content:
          JSON.stringify(
            {
              task: "Describe intended behavior",
              expectLine: ["expected output"],
              args: [],
            },
            null,
            2,
          ) + "\n",
      });
      await vscode.window.showTextDocument(document);
      vscode.window.showInformationMessage(
        "Save inputs as .inspect-cj/request.json in your workspace; use exact expected behavior.",
      );
    },
  };
  for (const [name, handler] of Object.entries(commands))
    context.subscriptions.push(
      vscode.commands.registerCommand(`inspectCJ.${name}`, async (...args) => {
        try {
          return await handler(...args);
        } catch (error) {
          output.appendLine(error.stack || error.message);
          vscode.window.showErrorMessage(`InspectCJ: ${error.message}`);
          return { code: 2, json: { status: "failed", reason: error.message } };
        }
      }),
    );
}
module.exports = { activate };
