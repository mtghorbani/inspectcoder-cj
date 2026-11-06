"use strict";
const path = require("node:path");
const fs = require("node:fs/promises");

async function prepareHost(
  extensionRoot,
  sourceRoot = path.resolve(__dirname, ".."),
) {
  const source = await fs.realpath(sourceRoot);
  const extension = await fs.realpath(extensionRoot);
  const sourceHarness = path.join(source, "test", "host.js");
  if (source === extension) return sourceHarness;
  const directory = path.join(extension, "test");
  await fs.mkdir(directory, { recursive: true });
  const harness = path.join(directory, "host.js");
  await fs.copyFile(sourceHarness, harness);
  await fs.copyFile(
    path.join(source, "test", "cli-environment.js"),
    path.join(directory, "cli-environment.js"),
  );
  return harness;
}

module.exports = { prepareHost };
