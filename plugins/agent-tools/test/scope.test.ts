import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdtemp,
  mkdir,
  cp,
  readFile,
  writeFile,
  symlink,
  realpath,
  readdir,
} from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import opencode from "../src/opencode.js";

const enabled = process.env.INSPECT_CJ_PLUGIN_NATIVE === "1";
async function fixture() {
  const base = await realpath(
    await mkdtemp(path.join(tmpdir(), "inspect-scope-")),
  );
  const cwd = path.join(base, "host");
  await mkdir(cwd);
  const hooks = await opencode({ directory: cwd } as never);
  return {
    base,
    cwd,
    call: async (name: string, args: Record<string, unknown>) =>
      JSON.parse(
        (await hooks.tool![`inspect_cj_${name}`].execute(args, {
          directory: cwd,
          abort: new AbortController().signal,
          ask: async () => {},
        } as never)) as string,
      ),
  };
}
function refused(outcome: any) {
  assert.equal(outcome.code, 2, JSON.stringify(outcome));
  const failure = outcome.result.observations?.[0] ?? outcome.result;
  assert.equal(failure.status, "failed", JSON.stringify(outcome));
  assert.match(failure.reason, /trust the containing workspace/);
}

test(
  "adapter refuses repair through an external project.json symlink",
  { skip: !enabled },
  async () => {
    const { base, cwd, call } = await fixture();
    const external = path.join(base, "external");
    await cp(path.resolve("../../examples/sum"), external, { recursive: true });
    await mkdir(path.join(cwd, "fixtures"));
    await symlink(
      path.join(external, "project.json"),
      path.join(cwd, "fixtures/project.json"),
    );
    await cp(
      path.join(external, "offline.json"),
      path.join(cwd, "script.json"),
    );
    const runs = path.join(
      process.env.XDG_CACHE_HOME ?? path.join(homedir(), ".cache"),
      "inspect-cj/runs",
    );
    const before = await readdir(runs).catch(() => []);
    refused(
      await call("repair", {
        target: "fixtures",
        transport: "scripted",
        script: "script.json",
      }),
    );
    assert.deepEqual(await readdir(runs).catch(() => []), before);
  },
);
for (const operation of ["repair", "test", "debug"]) {
  test(
    `adapter refuses ${operation} discovery of a workspace above host cwd`,
    { skip: !enabled },
    async () => {
      const { base, cwd, call } = await fixture();
      await writeFile(
        path.join(base, "cjpm.toml"),
        '[workspace]\nmembers = ["host"]\n',
      );
      await cp(path.resolve("../../examples/native"), cwd, { recursive: true });
      const source = await readFile(path.join(cwd, "src/lib.cj"), "utf8");
      await writeFile(
        path.join(base, "external-secret.cj"),
        "external source must never be loaded",
      );
      await writeFile(
        path.join(cwd, "script.json"),
        JSON.stringify([
          { role: "coder", response: { source, summary: "unchanged" } },
        ]),
      );
      refused(
        await call(operation, {
          target: "src/lib.cj",
          ...(operation === "repair"
            ? { transport: "scripted", script: "script.json" }
            : {}),
          ...(operation === "debug"
            ? { actions: [{ action: "inspect", variable: "x" }] }
            : {}),
        }),
      );
      assert.equal(
        await readFile(path.join(cwd, "src/lib.cj"), "utf8"),
        source,
      );
    },
  );
}

test(
  "adapter apply refuses recorded acceptance outside host with private artifacts outside cwd",
  { skip: !enabled },
  async () => {
    const { base, cwd, call } = await fixture();
    const run = path.join(base, "private-run");
    await mkdir(run);
    const original = "main() { 0 }\n";
    const candidate = "main() { 1 }\n";
    const target = path.join(cwd, "main.cj");
    const acceptance = path.join(base, "acceptance.txt");
    const hash = (text: string) =>
      createHash("sha256").update(text).digest("hex");
    await writeFile(target, original);
    await writeFile(acceptance, "independent");
    await writeFile(
      path.join(run, "invocation.json"),
      JSON.stringify({
        schema_version: 1,
        root: cwd,
        config: { editable: "main.cj" },
        sources: { "main.cj": { path: target, sha256: hash(original) } },
        inputs: [{ path: acceptance, sha256: hash("independent") }],
      }),
    );
    await writeFile(
      path.join(run, "report.json"),
      JSON.stringify({ status: "success" }),
    );
    await writeFile(
      path.join(run, "candidate.json"),
      JSON.stringify({
        target,
        editable: "main.cj",
        base_sha256: hash(original),
        candidate_sha256: hash(candidate),
      }),
    );
    await writeFile(path.join(run, "repaired.cj"), candidate);
    refused(await call("apply", { run }));
    assert.equal(await readFile(target, "utf8"), original);
  },
);
