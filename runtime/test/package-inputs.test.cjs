"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

test("package builds synchronize shared runtime, MIT grant and attribution from root inputs", (t) => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "inspect cj package inputs "),
  );
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "scripts"));
  fs.mkdirSync(path.join(root, "runtime"));
  fs.copyFileSync(
    path.resolve(__dirname, "../../scripts/copy-runtime.cjs"),
    path.join(root, "scripts/copy-runtime.cjs"),
  );
  fs.writeFileSync(
    path.join(root, "runtime/cli.cjs"),
    "module.exports = { shared: true };\n",
  );
  fs.writeFileSync(
    path.join(root, "runtime/cli.d.cts"),
    "export const shared: true;\n",
  );
  fs.writeFileSync(path.join(root, "LICENSE"), "MIT grant fixture\n");
  fs.writeFileSync(path.join(root, "NOTICE.md"), "Attribution fixture\n");
  const result = spawnSync(
    process.execPath,
    [path.join(root, "scripts/copy-runtime.cjs")],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  for (const directory of ["plugins/agent-tools", "editors/vscode"]) {
    assert.equal(fs.existsSync(path.join(root, directory, "LICENSE")), true);
    assert.equal(
      fs.readFileSync(path.join(root, directory, "LICENSE"), "utf8"),
      "MIT grant fixture\n",
    );
    assert.equal(
      fs.readFileSync(path.join(root, directory, "NOTICE.md"), "utf8"),
      "Attribution fixture\n",
    );
    assert.equal(
      fs.readFileSync(path.join(root, directory, "runtime/cli.cjs"), "utf8"),
      "module.exports = { shared: true };\n",
    );
    assert.equal(
      fs.readFileSync(path.join(root, directory, "runtime/cli.d.cts"), "utf8"),
      "export const shared: true;\n",
    );
  }
});
