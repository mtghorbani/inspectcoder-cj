const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { buildArgs, checkContext, runCli } = require("../core");

test("standalone request preserves exact inputs and literal argv without shell interpretation", () => {
  const args = buildArgs("file", "/tmp/a b.cj", {
    task: "Print input",
    expectLine: ["one", "two"],
    args: ["$(touch nope)", "--literal"],
    stdinFile: "/tmp/input data",
    driver: "/tmp/driver.cj",
    expectExit: 7,
  });
  assert.deepEqual(args, [
    "repair",
    "/tmp/a b.cj",
    "--non-interactive",
    "--task",
    "Print input",
    "--driver",
    "/tmp/driver.cj",
    "--stdin-file",
    "/tmp/input data",
    "--expect-line",
    "one",
    "--expect-line",
    "two",
    "--expect-exit",
    "7",
    "--",
    "$(touch nope)",
    "--literal",
  ]);
});

test("requests reject typos, shell stdin and ambiguous expectation flags", () => {
  for (const request of [
    { typo: 1 },
    { args: "4" },
    { task: "-" },
    { stdinFile: "-" },
    { expectLine: ["4"], expectStdout: "4" },
    { runTimeout: -1 },
    { expectExit: 1.5 },
  ]) {
    assert.throws(() => buildArgs("file", "/tmp/main.cj", request));
  }
  assert.throws(() =>
    buildArgs("package", "/tmp/package", { expectLine: ["guessed"] }),
  );
});

test("execution requires trusted saved local paths on the workspace host", () => {
  const context = {
    trusted: true,
    remote: false,
    platform: "darwin",
    scheme: "file",
    target: "/tmp/project/main.cj",
    workspace: "/tmp/project",
    dirty: false,
  };
  checkContext(context);
  for (const change of [
    { trusted: false },
    { remote: true },
    { platform: "win32" },
    { scheme: "vscode-remote" },
    { dirty: true },
    { target: "/tmp/project-other/main.cj" },
  ]) {
    assert.throws(() => checkContext({ ...context, ...change }));
  }
});

test("actual child receives argv literally and emits parsed CLI errors", async () => {
  const r = await runCli(
    process.execPath,
    [
      "-e",
      'console.log(JSON.stringify({status:"needs_input",args:process.argv.slice(1)}));process.exitCode=5',
      "a; echo nope",
      "$(touch nope)",
    ],
    { cwd: os.tmpdir() },
  );
  assert.equal(r.code, 5);
  assert.equal(r.json.status, "needs_input");
  assert.deepEqual(r.json.args, ["a; echo nope", "$(touch nope)"]);
  await assert.rejects(
    runCli(process.execPath, ["-e", 'console.log("bad output")'], {
      cwd: os.tmpdir(),
    }),
    /JSON/,
  );
});

test("cancellation signals the real child and waits for its cleanup receipt", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "inspect-cj-node-"));
  try {
    const script = path.join(dir, "child.js");
    await fs.writeFile(
      script,
      'process.on("SIGINT",()=>{console.log(JSON.stringify({status:"interrupted"}));process.exit(130)});require("fs").writeFileSync(process.argv[2],"ready");setInterval(()=>{},100);',
    );
    const signal = new AbortController();
    const promise = runCli(
      process.execPath,
      [script, path.join(dir, "ready")],
      { cwd: dir, signal: signal.signal },
    );
    const limit = Date.now() + 5000;
    while (!(await fs.stat(path.join(dir, "ready")).catch(() => null))) {
      if (Date.now() > limit) throw new Error("child did not start");
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    signal.abort();
    assert.equal((await promise).json.status, "interrupted");
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});
