import assert from "node:assert/strict";
import { spawnSync, execFileSync } from "node:child_process";
import { mkdtemp, mkdir, writeFile, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { test } from "node:test";

for (const host of ["opencode", "pi"]) {
  for (const [operation, artifact] of [
    ["review", "invocation.json"],
    ["review", "candidate.diff"],
    ["apply", "invocation.json"],
  ]) {
    test(
      `${host} ${operation} rejects FIFO ${artifact} without blocking`,
      { skip: process.platform === "win32" },
      async () => {
        const cwd = await realpath(
          await mkdtemp(path.join(tmpdir(), "inspect-artifact-")),
        );
        const run = path.join(cwd, "run");
        await mkdir(run);
        if (artifact !== "invocation.json") {
          await writeFile(
            path.join(run, "invocation.json"),
            JSON.stringify({ root: cwd }),
          );
        }
        execFileSync("mkfifo", [path.join(run, artifact)]);
        const entry = pathToFileURL(path.resolve(`src/${host}.ts`)).href;
        const script = `
        import extension from ${JSON.stringify(entry)};
        const controller = new AbortController();
        const cwd = ${JSON.stringify(cwd)}, run = ${JSON.stringify(run)};
        let call;
        if (${JSON.stringify(host)} === 'opencode') {
          const hooks = await extension({directory:cwd});
          call = () => hooks.tool[${JSON.stringify(`inspect_cj_${operation}`)}].execute({run},{directory:cwd,abort:controller.signal,ask:async()=>{throw new Error('permission must not be requested for invalid artifact');}});
        } else {
          const tools = new Map();
          extension({registerTool:tool=>tools.set(tool.name,tool)});
          call = () => tools.get(${JSON.stringify(`inspect_cj_${operation}`)}).execute('test',{run},controller.signal,undefined,{cwd,hasUI:true,ui:{confirm:async()=>{throw new Error('permission must not be requested for invalid artifact');}}});
        }
        try { await call(); process.exitCode=2; }
        catch (error) { console.log(error.message); process.exitCode=/regular file/.test(error.message)?0:3; }
      `;
        const child = spawnSync(
          process.execPath,
          ["--import", "tsx", "--input-type=module", "-e", script],
          {
            cwd: process.cwd(),
            timeout: 5000,
            killSignal: "SIGKILL",
            encoding: "utf8",
          },
        );
        assert.equal(
          child.status,
          0,
          `artifact read did not reject promptly: ${child.error?.message ?? child.stderr}; signal=${child.signal}`,
        );
        assert.match(child.stdout, /regular file/);
      },
    );
  }
}
