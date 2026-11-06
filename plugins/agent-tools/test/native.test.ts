import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, realpath, cp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import opencode from "../src/opencode.js";
import piExtension from "../src/pi.js";
import {
  createEventBus,
  createExtensionRuntime,
} from "@earendil-works/pi-coding-agent";
import { loadExtensionFromFactory } from "../node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js";

const enabled = process.env.INSPECT_CJ_PLUGIN_NATIVE === "1";

test(
  "both host tools run real compiler, debugger, repair, review and guarded apply",
  { skip: !enabled, timeout: 240000 },
  async () => {
    for (const host of ["opencode", "pi"]) {
      const cwd = await realpath(
        await mkdtemp(path.join(tmpdir(), `inspect-native-${host}-`)),
      );
      await cp(path.resolve("../../examples/sum"), cwd, { recursive: true });
      const before = await readFile(path.join(cwd, "main.cj"), "utf8");
      let permissions = 0;
      let call: (name: string, args: Record<string, unknown>) => Promise<any>;
      if (host === "opencode") {
        const hooks = await opencode({ directory: cwd } as never);
        call = async (name, args) =>
          JSON.parse(
            (await hooks.tool![`inspect_cj_${name}`].execute(args, {
              directory: cwd,
              abort: new AbortController().signal,
              ask: async (permission: any) => {
                assert.equal(permission.permission, "edit");
                permissions++;
              },
            } as never)) as string,
          );
      } else {
        const extension = await loadExtensionFromFactory(
          piExtension,
          cwd,
          createEventBus(),
          createExtensionRuntime(),
        );
        call = async (name, args) =>
          (
            await extension.tools
              .get(`inspect_cj_${name}`)!
              .definition.execute("native-test", args, undefined, undefined, {
                cwd,
                hasUI: true,
                ui: {
                  confirm: async () => {
                    permissions++;
                    return true;
                  },
                },
              } as never)
          ).details;
      }
      const doctor = await call("doctor", {});
      assert.equal(doctor.result.status, "success", JSON.stringify(doctor));
      const baseline = await call("test", { target: "project.json" });
      assert.equal(baseline.code, 1);
      const debug = await call("debug", {
        target: "project.json",
        actions: [
          { action: "set_breakpoint", line: 8 },
          { action: "control_execution", command: "continue" },
          { action: "inspect", variable: "total", frame: 0 },
        ],
      });
      assert.equal(debug.code, 0, JSON.stringify(debug));
      assert.ok(
        debug.result.observations.some((observation: any) =>
          observation.output?.includes("total = 6"),
        ),
      );
      const repaired = await call("repair", {
        target: "project.json",
        transport: "scripted",
        script: "offline.json",
      });
      assert.equal(repaired.result.status, "success", JSON.stringify(repaired));
      const run = repaired.result.output;
      assert.equal(await readFile(path.join(cwd, "main.cj"), "utf8"), before);
      const review = await call("review", { run });
      assert.match(review.result.diff, /1\.\.=n/);
      const applied = await call("apply", { run });
      assert.equal(applied.result.status, "success", JSON.stringify(applied));
      assert.equal(permissions, 1);
      assert.notEqual(
        await readFile(path.join(cwd, "main.cj"), "utf8"),
        before,
      );
      const verified = await call("test", { target: "project.json" });
      assert.equal(verified.result.status, "success", JSON.stringify(verified));
      await writeFile(path.join(cwd, "main.cj"), before + "\n// user edit\n");
      const stale = await call("apply", { run });
      assert.equal(stale.result.status, "failed", JSON.stringify(stale));
      assert.match(
        await readFile(path.join(cwd, "main.cj"), "utf8"),
        /user edit/,
      );
      // Native assertions, including both test cases, remain the acceptance contract.
      const native = path.join(cwd, "native");
      await cp(path.resolve("../../examples/native"), native, {
        recursive: true,
      });
      const library = path.join(native, "src/lib.cj");
      const fixed = (await readFile(library, "utf8")).replace(
        "return size }",
        "return size - 1 }",
      );
      await writeFile(
        library,
        fixed.replace("return index", 'return "type error"'),
      );
      const testsBefore = await readFile(
        path.join(native, "src/lib_test.cj"),
        "utf8",
      );
      await writeFile(
        path.join(cwd, "native-script.json"),
        JSON.stringify([
          {
            role: "coder",
            response: { source: fixed, summary: "Restore Int64 return" },
          },
        ]),
      );
      assert.equal((await call("test", { target: "native" })).code, 2);
      const nativeRepair = await call("repair", {
        target: "native",
        transport: "scripted",
        script: "native-script.json",
      });
      assert.equal(
        nativeRepair.result.status,
        "success",
        JSON.stringify(nativeRepair),
      );
      const nativeRun = nativeRepair.result.output;
      assert.match(
        (await call("review", { run: nativeRun })).result.diff,
        /type error/,
      );
      assert.equal(
        (await call("apply", { run: nativeRun })).result.status,
        "success",
      );
      const nativePassed = await call("test", { target: "native" });
      assert.equal(
        nativePassed.result.status,
        "success",
        JSON.stringify(nativePassed),
      );
      assert.equal(nativePassed.result.tests.length, 2);
      assert.equal(
        await readFile(path.join(native, "src/lib_test.cj"), "utf8"),
        testsBefore,
      );
    }
  },
);
