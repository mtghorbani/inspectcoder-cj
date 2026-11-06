"use strict";
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const destinations = {
  "agent-tools": "plugins/agent-tools/runtime",
  vscode: "editors/vscode/runtime",
};
const requested = process.argv.slice(2);
const targets = requested.length ? requested : Object.keys(destinations);
for (const target of targets) {
  if (!Object.hasOwn(destinations, target))
    throw new Error(
      `Unknown runtime target: ${target}. Use agent-tools or vscode.`,
    );
}
for (const target of targets) {
  const directory = path.join(root, destinations[target]);
  fs.mkdirSync(directory, { recursive: true });
  for (const name of ["LICENSE", "NOTICE.md"])
    fs.copyFileSync(path.join(root, name), path.join(directory, "..", name));
  for (const name of ["cli.cjs", "cli.d.cts"])
    fs.copyFileSync(
      path.join(root, "runtime", name),
      path.join(directory, name),
    );
}
