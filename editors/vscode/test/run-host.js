"use strict";
const path = require("node:path");
const fs = require("node:fs/promises");
const { runTests } = require("@vscode/test-electron");
const { prepareHost } = require("./prepare-host");

async function main() {
  const root = process.env.INSPECT_CJ_EDITOR_TEST_ROOT;
  const executable = process.env.VSCODE_EXECUTABLE;
  if (
    !root ||
    !path.isAbsolute(root) ||
    !executable ||
    !process.env.INSPECT_CJ_TEST_CLI
  )
    throw new Error(
      "Set absolute INSPECT_CJ_EDITOR_TEST_ROOT, VSCODE_EXECUTABLE and INSPECT_CJ_TEST_CLI",
    );
  const workspace = path.join(root, "workspace");
  await fs.mkdir(workspace, { recursive: true });
  // Start in workspace mode so adding the boundary-test folder needs no UI transition.
  const workspaceFile = path.join(root, "test.code-workspace");
  await fs.writeFile(
    workspaceFile,
    JSON.stringify({ folders: [{ path: workspace }] }),
  );
  const extensionRoot =
    process.env.INSPECT_CJ_EXTENSION_ROOT || path.resolve(__dirname, "..");
  const extensionTestsPath = await prepareHost(extensionRoot);
  const exit = await runTests({
    vscodeExecutablePath: executable,
    extensionDevelopmentPath: extensionRoot,
    extensionTestsPath,
    launchArgs: [
      workspaceFile,
      "--user-data-dir",
      path.join(root, "profile"),
      "--extensions-dir",
      path.join(root, "extensions"),
      "--disable-extensions",
      "--disable-workspace-trust",
      "--skip-welcome",
      "--skip-release-notes",
      "--disable-updates",
      "--disable-telemetry",
    ],
    extensionTestsEnv: { ...process.env, INSPECT_CJ_TEST_WORKSPACE: workspace },
  });
  if (exit) process.exitCode = exit;
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
