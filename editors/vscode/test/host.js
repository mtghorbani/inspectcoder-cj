"use strict";
const vscode = require("vscode");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { runCli } = require("../core");
const { clearCliOverrides } = require("./cli-environment");

const results = [];
let phase = "startup";

function start(name) {
  phase = name;
  require("node:fs").writeFileSync(
    path.join(
      process.env.INSPECT_CJ_TEST_WORKSPACE,
      "..",
      "host-progress.json",
    ),
    JSON.stringify({ phase, results }),
  );
  console.log(`START: ${name}`);
}

async function runSuite() {
  clearCliOverrides(process.env);
  const root = process.env.INSPECT_CJ_TEST_WORKSPACE;
  const cli = process.env.INSPECT_CJ_TEST_CLI;
  const check = async (name, action) => {
    start(name);
    await action();
    results.push(name);
    console.log(`PASS: ${name}`);
  };
  await check(
    "installed CLI doctor uses isolated config without legacy SDK overrides",
    async () => {
      const doctor = await runCli(cli, ["doctor"], { cwd: root });
      assert.equal(doctor.json.status, "success", JSON.stringify(doctor));
      if (process.env.INSPECT_CJ_CONFIG) {
        const config = JSON.parse(
          await fs.readFile(process.env.INSPECT_CJ_CONFIG, "utf8"),
        );
        if (config.sdk) {
          assert.equal(
            doctor.json.cjc_path,
            path.join(config.sdk, "bin", "cjc"),
          );
          assert.equal(
            doctor.json.cjdb_path,
            path.join(config.sdk, "third_party", "llvm", "bin", "lldb"),
          );
        }
      }
    },
  );
  start("configure executable");
  const config = vscode.workspace.getConfiguration("inspectCJ");
  await config.update("executable", cli, vscode.ConfigurationTarget.Workspace);
  start("configure transport");
  await config.update(
    "transport",
    "scripted",
    vscode.ConfigurationTarget.Workspace,
  );
  start("configure scripted responses");
  const script = path.join(root, "responses.json");
  await config.update("script", script, vscode.ConfigurationTarget.Workspace);
  const uri = vscode.Uri.file(path.join(root, "main.cj"));
  const original = 'main() { let value: Int64 = "bad"; println(value); 0 }\n';
  const fixed = "main() { println(42); 0 }\n";
  await fs.writeFile(uri.fsPath, original);
  await fs.writeFile(
    script,
    JSON.stringify([
      {
        role: "coder",
        response: { source: fixed, summary: "Correct type and print 42" },
      },
    ]),
  );
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  let success;
  await check(
    "current-file compile-first actual CLI repair, immutable original and diff",
    async () => {
      success = await vscode.commands.executeCommand(
        "inspectCJ.repairFile",
        uri,
        { task: "Print 42", expectLine: ["42"] },
      );
      assert.equal(success.json.status, "success", JSON.stringify(success));
      assert.equal(success.json.patch_attempts, 1);
      assert.equal(await fs.readFile(uri.fsPath, "utf8"), original);
      const report = JSON.parse(
        await fs.readFile(path.join(success.json.output, "report.json")),
      );
      assert.notEqual(report.iterations[0].build.returncode, 0);
      assert.ok(report.iterations.at(-1).tests.every((t) => t.passed));
      assert.ok(
        vscode.window.tabGroups.all.some((group) =>
          group.tabs.some((tab) => tab.label.includes("Original / Candidate")),
        ),
      );
    },
  );
  await check("missing expectation yields structured diagnostic", async () => {
    const result = await vscode.commands.executeCommand(
      "inspectCJ.repairFile",
      uri,
      {},
    );
    assert.equal(result.json.status, "needs_input");
    assert.ok(
      vscode.languages
        .getDiagnostics(uri)
        .some((d) => d.code === "needs_input"),
    );
  });
  await check(
    "stale apply refuses changed disk source; explicit fresh apply succeeds",
    async () => {
      await fs.writeFile(uri.fsPath, "// User changed the source\n" + original);
      const refused = await vscode.commands.executeCommand(
        "inspectCJ.applyCandidate",
        success.json.output,
      );
      assert.equal(refused.code, 2);
      assert.match(refused.json.reason, /changed/);
      await fs.writeFile(uri.fsPath, original);
      const applied = await vscode.commands.executeCommand(
        "inspectCJ.applyCandidate",
        success.json.output,
      );
      assert.equal(applied.json.status, "success");
      assert.equal(await fs.readFile(uri.fsPath, "utf8"), fixed);
    },
  );
  await check("dirty editor refuses repair before launching CLI", async () => {
    const edit = new vscode.WorkspaceEdit();
    edit.insert(uri, new vscode.Position(0, 0), "// unsaved\n");
    await vscode.workspace.applyEdit(edit);
    const refused = await vscode.commands.executeCommand(
      "inspectCJ.repairFile",
      uri,
      { expectLine: ["42"] },
    );
    assert.match(refused.json.reason, /Save/);
    await vscode.window.showTextDocument(doc);
    await vscode.commands.executeCommand("workbench.action.files.revert");
  });
  await check(
    "member-only workspace refuses expansion for every repair command",
    async () => {
      const containing = path.join(root, "..", "boundary");
      const member = path.join(containing, "memberA");
      const sibling = path.join(containing, "memberB");
      await fs.mkdir(path.join(member, "src"), { recursive: true });
      await fs.mkdir(path.join(sibling, "src"), { recursive: true });
      await fs.writeFile(
        path.join(containing, "cjpm.toml"),
        '[workspace]\nmembers = ["memberA", "memberB"]\n',
      );
      await fs.writeFile(
        path.join(member, "cjpm.toml"),
        '[package]\nname="memberA"\nversion="1.0.0"\ncjc-version="1.1.0"\noutput-type="static"\n',
      );
      const source = path.join(member, "src/lib.cj");
      await fs.writeFile(
        source,
        "package memberA\npublic func value() { 42 }\n",
      );
      const outside = path.join(sibling, "src/outside.cj");
      await fs.symlink(source, outside);
      const changed = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          subscription.dispose();
          reject(new Error("Workspace-folder change timed out"));
        }, 15000);
        const subscription = vscode.workspace.onDidChangeWorkspaceFolders(
          () => {
            clearTimeout(timer);
            subscription.dispose();
            resolve();
          },
        );
      });
      const index = vscode.workspace.workspaceFolders.length;
      assert.ok(
        vscode.workspace.updateWorkspaceFolders(index, 0, {
          uri: vscode.Uri.file(member),
        }),
      );
      await changed;
      await vscode.workspace
        .getConfiguration("inspectCJ", vscode.Uri.file(source))
        .update("executable", cli, vscode.ConfigurationTarget.WorkspaceFolder);
      try {
        for (const command of [
          "repairFile",
          "repairPackage",
          "repairSelectedTest",
        ]) {
          const result = await vscode.commands.executeCommand(
            `inspectCJ.${command}`,
            vscode.Uri.file(source),
            {},
          );
          assert.notEqual(result.code, 0);
          assert.match(
            result.json.reason,
            /Open and trust the containing workspace/,
          );
          assert.equal(result.json.output, undefined);
        }
        assert.equal(
          await fs.readFile(source, "utf8"),
          "package memberA\npublic func value() { 42 }\n",
        );
        assert.ok((await fs.lstat(outside)).isSymbolicLink());
      } finally {
        vscode.workspace.updateWorkspaceFolders(index, 1);
      }
    },
  );
  const pkg = path.join(root, "package");
  await fs.mkdir(path.join(pkg, "src"), { recursive: true });
  await fs.writeFile(
    path.join(pkg, "cjpm.toml"),
    '[package]\nname="bounds"\nversion="1.0.0"\ncjc-version="1.1.0"\noutput-type="static"\n[dependencies]\n',
  );
  const lib = path.join(pkg, "src/lib.cj");
  const impl =
    'package bounds\npublic func value() { let bad: Int64 = "bad"; bad }\n';
  await fs.writeFile(lib, impl);
  await fs.writeFile(
    path.join(pkg, "src/lib_test.cj"),
    "package bounds\nimport std.unittest.*\nimport std.unittest.testmacro.*\n@Test\npublic class Values {\n@TestCase\nfunc answer() { @Expect(value(), 42) }\n@TestCase\nfunc positive() { @Expect(value() > 0, true) }\n}\n",
  );
  let native;
  await check("package compile-first real cjpm full-suite repair", async () => {
    await fs.writeFile(
      script,
      JSON.stringify([
        {
          role: "coder",
          response: {
            source: "package bounds\npublic func value() { 42 }\n",
            summary: "Correct implementation",
          },
        },
      ]),
    );
    native = await vscode.commands.executeCommand(
      "inspectCJ.repairPackage",
      vscode.Uri.file(lib),
      {},
    );
    assert.equal(native.json.status, "success", JSON.stringify(native));
    assert.equal(await fs.readFile(lib, "utf8"), impl);
    const report = JSON.parse(
      await fs.readFile(path.join(native.json.output, "report.json")),
    );
    assert.equal(report.iterations.at(-1).tests.length, 2);
    assert.ok(report.iterations.at(-1).tests.every((t) => t.passed));
  });
  await check(
    "current package source uses native assertions without an expectation prompt",
    async () => {
      let timer;
      try {
        const result = await Promise.race([
          vscode.commands.executeCommand(
            "inspectCJ.repairFile",
            vscode.Uri.file(lib),
          ),
          new Promise((_, reject) => {
            timer = setTimeout(
              () =>
                reject(
                  new Error(
                    "Native current-file command requested standalone input",
                  ),
                ),
              15000,
            );
          }),
        ]);
        assert.equal(result.json.status, "success", JSON.stringify(result));
      } finally {
        clearTimeout(timer);
      }
    },
  );
  await check(
    "selected opaque native test executes through extension and same CLI",
    async () => {
      await fs.writeFile(
        lib,
        "package bounds\npublic func value() {\n    let answer = 41\n    return answer\n}\n",
      );
      await fs.writeFile(
        script,
        JSON.stringify([
          {
            role: "inspector",
            response: {
              thought: "Inspect actual answer",
              actions: [
                { action: "set_breakpoint", line: 4 },
                { action: "control_execution", command: "continue" },
                { action: "inspect", variable: "answer" },
                {
                  action: "propose_repair",
                  plan: "Return the independently asserted value 42",
                  evidence: ["obs-7"],
                },
              ],
            },
          },
          {
            role: "coder",
            response: {
              source: "package bounds\npublic func value() { 42 }\n",
              summary: "Correct answer",
            },
          },
        ]),
      );
      const tested = await runCli(cli, ["test", pkg], { cwd: root });
      assert.equal(tested.code, 1);
      const failed = tested.json.tests.find((t) => !t.passed);
      const selected = await vscode.commands.executeCommand(
        "inspectCJ.repairSelectedTest",
        vscode.Uri.file(lib),
        { case: failed.name },
      );
      assert.equal(selected.json.status, "success", JSON.stringify(selected));
      const report = JSON.parse(
        await fs.readFile(path.join(selected.json.output, "report.json")),
      );
      assert.equal(report.iterations[0].tests.length, 2);
      assert.equal(report.iterations[0].tests[0].name, failed.name);
      const events = await fs.readFile(
        path.join(selected.json.output, "events.jsonl"),
        "utf8",
      );
      assert.ok(events.includes("answer = 41"));
      assert.equal(selected.json.patch_attempts, 1);
    },
  );
  await check(
    "cancel command interrupts real native executable and returns cleanup receipt",
    async () => {
      await fs.writeFile(uri.fsPath, "main() { while (true) {} }\n");
      const pending = vscode.commands.executeCommand(
        "inspectCJ.repairFile",
        uri,
        { expectLine: ["done"], runTimeout: 60 },
      );
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await vscode.commands.executeCommand("inspectCJ.cancel");
      const cancelled = await pending;
      assert.equal(
        cancelled.json.status,
        "interrupted",
        JSON.stringify(cancelled),
      );
      assert.equal(cancelled.code, 130);
    },
  );
  await fs.writeFile(
    path.join(root, "..", "host-results.json"),
    JSON.stringify(
      {
        status: "success",
        vscode: vscode.version,
        platform: process.platform,
        results,
      },
      null,
      2,
    ) + "\n",
  );
}
async function run() {
  try {
    await runSuite();
  } catch (error) {
    const root = process.env.INSPECT_CJ_TEST_WORKSPACE;
    console.error(`FAIL: ${phase}: ${error.stack || error.message}`);
    await fs.writeFile(
      path.join(root, "..", "host-results.json"),
      JSON.stringify(
        {
          status: "failed",
          phase,
          results,
          error: error.stack || error.message,
        },
        null,
        2,
      ) + "\n",
    );
    throw error;
  }
}
module.exports = { run };
