"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { prepareHost } = require("./prepare-host");

test("external host harness loads the installed extension core", async (t) => {
  const root = await fs.mkdtemp(
    path.join(os.tmpdir(), "inspect cj installed host "),
  );
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const installed = path.join(root, "installed extension");
  await fs.mkdir(path.join(source, "test"), { recursive: true });
  await fs.mkdir(installed);
  await fs.writeFile(
    path.join(source, "core.js"),
    'module.exports = "source core";\n',
  );
  await fs.writeFile(
    path.join(installed, "core.js"),
    'module.exports = "packaged core";\n',
  );
  await fs.writeFile(
    path.join(source, "test/host.js"),
    'exports.run = () => require("../core");\n',
  );
  await fs.copyFile(
    path.join(__dirname, "cli-environment.js"),
    path.join(source, "test", "cli-environment.js"),
  );
  const harness = await prepareHost(installed, source);
  assert.equal(require(harness).run(), "packaged core");
  const environment = { HOME: "/real/home", CANGJIE_HOME: "/stale/sdk" };
  require(
    path.join(path.dirname(harness), "cli-environment.js"),
  ).clearCliOverrides(environment);
  assert.deepEqual(environment, { HOME: "/real/home" });
  assert.equal(
    path.dirname(path.dirname(harness)),
    await fs.realpath(installed),
  );
  assert.equal(
    await fs.readFile(path.join(source, "core.js"), "utf8"),
    'module.exports = "source core";\n',
  );
});

test("source extension tests use their existing harness directly", async () => {
  const source = path.resolve(__dirname, "..");
  assert.equal(
    await prepareHost(source),
    path.join(await fs.realpath(source), "test/host.js"),
  );
});
