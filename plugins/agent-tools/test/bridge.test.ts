import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, writeFile, mkdir, symlink, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { executeOperation, reviewRun } from "../src/bridge.js";

async function fixture(
  source = 'console.log(JSON.stringify({status:"success", argv:process.argv.slice(2)}))',
) {
  const cwd = await realpath(
    await mkdtemp(path.join(tmpdir(), "inspect cj plugin ")),
  );
  const cli = path.join(cwd, "cli.mjs");
  await writeFile(cli, "#!/usr/bin/env node\n" + source, { mode: 0o755 });
  await writeFile(path.join(cwd, "main.cj"), "main() { 0 }");
  return { cwd, executable: cli };
}

test("repair preserves literal arguments and never applies a candidate", async () => {
  const ctx = await fixture();
  const result = await executeOperation(
    "repair",
    {
      target: "main.cj",
      task: "print $(literal)",
      expectStdout: "10\n",
      args: ["a b", ";touch pwned"],
    },
    ctx,
  );
  assert.equal(result.code, 0);
  assert.deepEqual(result.result.argv, [
    "repair",
    path.join(ctx.cwd, "main.cj"),
    "--non-interactive",
    "--task=print $(literal)",
    "--expect-stdout=10\n",
    "--",
    "a b",
    ";touch pwned",
  ]);
});

test("rejects traversal and symlink escape before launch", async () => {
  const ctx = await fixture();
  const outside = await fixture();
  await symlink(outside.cwd, path.join(ctx.cwd, "link"));
  for (const target of ["../other.cj", "link/main.cj"]) {
    await assert.rejects(
      executeOperation("test", { target }, ctx),
      /inside.*project/i,
    );
  }
});

test("debug sends actions as JSONL and retains all observations", async () => {
  const ctx = await fixture(
    'let s=""; for await (const c of process.stdin) s+=c; console.log(JSON.stringify({type:"session"})); for (const l of s.trim().split("\\n")) console.log(JSON.stringify({status:"ok",action:JSON.parse(l).action}));',
  );
  const result = await executeOperation(
    "debug",
    { target: "main.cj", actions: [{ action: "inspect", variable: "x" }] },
    ctx,
  );
  assert.deepEqual(result.result.observations, [
    { type: "session" },
    { status: "ok", action: "inspect" },
  ]);
});

test("retains nonzero CLI outcomes as structured failures", async () => {
  const ctx = await fixture(
    'console.log(JSON.stringify({status:"needs_input",missing:["expectation"]})); process.exitCode=5;',
  );
  const result = await executeOperation("repair", { target: "main.cj" }, ctx);
  assert.equal(result.code, 5);
  assert.equal(result.result.status, "needs_input");
});

test("rejects malformed and oversized output", async () => {
  const bad = await fixture('console.log("not json")');
  await assert.rejects(executeOperation("doctor", {}, bad), /JSON/);
  const big = await fixture(
    'process.stdout.write("x".repeat(10000)); setInterval(()=>{},1000)',
  );
  await assert.rejects(
    executeOperation("doctor", {}, { ...big, maxOutputBytes: 1024 }),
    /output.*limit/i,
  );
});

test("cancellation and deadlines terminate a running process", async () => {
  const ctx = await fixture("setInterval(()=>{},1000)");
  const controller = new AbortController();
  const pending = executeOperation(
    "doctor",
    {},
    { ...ctx, signal: controller.signal },
  );
  setTimeout(() => controller.abort(), 100);
  await assert.rejects(pending, /cancel/i);
  await assert.rejects(
    executeOperation("doctor", {}, { ...ctx, timeoutMs: 100 }),
    /timed out/i,
  );
  await assert.rejects(
    executeOperation("doctor", {}, { ...ctx, signal: controller.signal }),
    /cancel/i,
  );
});

test("review validates recorded root and returns the candidate diff", async () => {
  const ctx = await fixture();
  const run = path.join(ctx.cwd, "run");
  await mkdir(run);
  await writeFile(
    path.join(run, "invocation.json"),
    JSON.stringify({ root: ctx.cwd }),
  );
  await writeFile(path.join(run, "candidate.diff"), "-wrong\n+right\n");
  assert.equal((await reviewRun(run, ctx.cwd)).diff, "-wrong\n+right\n");
  await writeFile(
    path.join(run, "invocation.json"),
    JSON.stringify({ root: tmpdir() }),
  );
  await assert.rejects(reviewRun(run, ctx.cwd), /inside.*project/i);
});

test("apply cannot be invoked without host authorization", async () => {
  const ctx = await fixture();
  await assert.rejects(
    executeOperation("apply", { run: ctx.cwd }, ctx),
    /host permission/,
  );
});

test("CLI launch errors explain the executable setting", async () => {
  const ctx = await fixture();
  await assert.rejects(
    executeOperation(
      "doctor",
      {},
      { ...ctx, executable: path.join(ctx.cwd, "absent") },
    ),
    /INSPECT_CJ/,
  );
});

test("children cannot hold the invocation open after their parent exits", async () => {
  const ctx = await fixture(
    'import {spawn} from "node:child_process"; spawn(process.execPath,["-e","setInterval(()=>{},1000)"],{stdio:["ignore",process.stdout,process.stderr]}); console.log(JSON.stringify({status:"success"})); process.exit(0)',
  );
  const result = await executeOperation(
    "doctor",
    {},
    { ...ctx, timeoutMs: 2000 },
  );
  assert.equal(result.result.status, "success");
});

test("CLI boundary uses canonical host cwd and overrides inheritance only in child", async () => {
  const ctx = await fixture(
    'console.log(JSON.stringify({status:"success",root:process.env.INSPECT_CJ_WORKSPACE_ROOT}))',
  );
  const alias = path.join(ctx.cwd, "alias");
  await symlink(ctx.cwd, alias);
  const inherited = process.env.INSPECT_CJ_WORKSPACE_ROOT;
  process.env.INSPECT_CJ_WORKSPACE_ROOT = path.parse(ctx.cwd).root;
  try {
    const outcome = await executeOperation(
      "doctor",
      {},
      { ...ctx, cwd: alias },
    );
    assert.equal(outcome.result.root, ctx.cwd);
    assert.equal(
      process.env.INSPECT_CJ_WORKSPACE_ROOT,
      path.parse(ctx.cwd).root,
    );
  } finally {
    if (inherited === undefined) delete process.env.INSPECT_CJ_WORKSPACE_ROOT;
    else process.env.INSPECT_CJ_WORKSPACE_ROOT = inherited;
  }
});

for (const source of ["PATH", "config", "user bin"]) {
  test(`host bridge discovers CLI through ${source} without INSPECT_CJ`, async () => {
    const ctx = await fixture();
    const env = { ...process.env };
    try {
      delete process.env.INSPECT_CJ;
      delete process.env.INSPECT_CJ_CONFIG;
      delete process.env.XDG_CONFIG_HOME;
      process.env.HOME = ctx.cwd;
      process.env.PATH = "";
      const bin = path.join(
        ctx.cwd,
        source === "user bin" ? ".local/bin" : "bin",
      );
      await mkdir(bin, { recursive: true });
      const cli = path.join(bin, "inspectcoder-cj");
      await writeFile(
        cli,
        `#!${process.execPath}\nconsole.log(JSON.stringify({status:"success",cli:process.argv[1],root:process.env.INSPECT_CJ_WORKSPACE_ROOT}))`,
        { mode: 0o755 },
      );
      if (source === "PATH") process.env.PATH = bin;
      if (source === "config") {
        const directory = path.join(ctx.cwd, ".config/inspect-cj");
        await mkdir(directory, { recursive: true });
        await writeFile(
          path.join(directory, "config.json"),
          JSON.stringify({ cli }),
        );
      }
      const result = await executeOperation("doctor", {}, { cwd: ctx.cwd });
      assert.equal(result.result.cli, cli);
      assert.equal(result.result.root, ctx.cwd);
      assert.equal(process.env.INSPECT_CJ, undefined);
    } finally {
      for (const key of Object.keys(process.env)) {
        if (!(key in env)) delete process.env[key];
      }
      Object.assign(process.env, env);
    }
  });
}
