import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, realpath, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import opencode from "../src/opencode.js";
import piExtension from "../src/pi.js";
import { z } from "zod";
import {
  createEventBus,
  createExtensionRuntime,
} from "@earendil-works/pi-coding-agent";
import { loadExtensionFromFactory } from "../node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js";

async function fixture() {
  const cwd = await realpath(
    await mkdtemp(path.join(tmpdir(), "inspect-host-")),
  );
  await writeFile(path.join(cwd, "main.cj"), "main(){0}");
  const run = path.join(cwd, "run");
  await mkdir(run);
  await writeFile(
    path.join(run, "invocation.json"),
    JSON.stringify({ root: cwd, config: { editable: "main.cj" } }),
  );
  return { cwd, run };
}

test("OpenCode registers validated tools and asks edit permission before apply", async () => {
  const { cwd, run } = await fixture();
  const hooks = await opencode({ directory: cwd } as never);
  assert.deepEqual(
    Object.keys(hooks.tool!).sort(),
    ["apply", "debug", "doctor", "repair", "review", "test"]
      .map((x) => "inspect_cj_" + x)
      .sort(),
  );
  const repair = hooks.tool!.inspect_cj_repair;
  assert.equal(z.object(repair.args).safeParse({ target: 123 }).success, false);
  let request: unknown;
  await assert.rejects(
    hooks.tool!.inspect_cj_apply.execute({ run }, {
      directory: cwd,
      abort: new AbortController().signal,
      ask: async (value: unknown) => {
        request = value;
        throw new Error("denied");
      },
    } as never),
    /denied/,
  );
  assert.equal((request as { permission: string }).permission, "edit");
  assert.deepEqual((request as { patterns: string[] }).patterns, [
    path.join(cwd, "main.cj"),
  ]);
});

test("real Pi loader registers schemas and refuses headless or declined application", async () => {
  const { cwd, run } = await fixture();
  const extension = await loadExtensionFromFactory(
    piExtension,
    cwd,
    createEventBus(),
    createExtensionRuntime(),
  );
  assert.equal(extension.tools.size, 6);
  const apply = extension.tools.get("inspect_cj_apply")!;
  assert.ok(apply);
  const execute = apply.definition.execute;
  await assert.rejects(
    execute("id", { run }, undefined, undefined, {
      cwd,
      hasUI: false,
    } as never),
    /interactive.*confirmation/i,
  );
  await assert.rejects(
    execute("id", { run }, undefined, undefined, {
      cwd,
      hasUI: true,
      ui: { confirm: async () => false },
    } as never),
    /declined/i,
  );
});
