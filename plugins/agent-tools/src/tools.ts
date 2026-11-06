import { z } from "zod";
import type { Operation } from "./bridge.js";

const text = (description: string) =>
  z.string().max(65536).describe(description);
const target = text(
  "Source, project.json, or cjpm package path inside the current project",
);
const run = text("Repair artifact directory returned by inspect_cj_repair");
const positive = z.number().int().positive();
const debuggerAction = z
  .object({
    action: z.enum([
      "set_breakpoint",
      "control_execution",
      "inspect",
      "interact_code",
    ]),
    line: positive.optional(),
    file: text("Source path").optional(),
    command: text("Debugger execution command").optional(),
    variable: text("Variable to inspect; omit or null for all locals")
      .nullable()
      .optional(),
    frame: z.number().int().nonnegative().optional(),
    code: text("Debugger expression or command").optional(),
  })
  .strict();

export const definitions = {
  doctor: {
    description:
      "Compile and run a native Cangjie probe, then prove CJDB breakpoint and variable inspection capabilities.",
    schema: z.object({}).strict(),
  },
  test: {
    description:
      "Compile and execute existing project.json cases or the complete native cjpm assertion suite. Returns actual failures; does not invent expected behavior.",
    schema: z.object({ target }).strict(),
  },
  debug: {
    description:
      "Run an ordered batch of CJDB actions in one persistent debugger session. Requires a project.json with a declared case or a native cjpm package. Returns real JSONL observations.",
    schema: z
      .object({
        target,
        case: text(
          "Opaque native case identity or manifest test name",
        ).optional(),
        actions: z.array(debuggerAction).min(1).max(100),
      })
      .strict(),
  },
  repair: {
    description:
      "Repair a Cangjie source or native package in a private snapshot. Supply independent expectations for standalone code; native assertions supply acceptance. Missing acceptance returns needs_input. Never changes original source. Default model transport is the authenticated Codex CLI, independent of the host model. Review the returned run before applying.",
    schema: z
      .object({
        target,
        task: text("Intended behavior from the user").optional(),
        expectStdout: text(
          "Independent exact expected stdout including newlines",
        ).optional(),
        expectStderr: text("Independent exact expected stderr").optional(),
        expectExit: z.number().int().optional(),
        expectLine: z
          .array(text("Expected line, newline appended"))
          .max(100)
          .optional(),
        args: z.array(text("Literal program argument")).max(100).optional(),
        driver: text(
          "Immutable assertion driver inside the project",
        ).optional(),
        stdinFile: text("Program stdin file inside the project").optional(),
        compileOnly: z.boolean().optional(),
        editable: text(
          "One implementation source relative to the native package",
        ).optional(),
        case: text("Opaque native test identity").optional(),
        transport: z.enum(["codex", "responses", "scripted"]).optional(),
        model: text("Explicit repair model override").optional(),
        script: text(
          "Deterministic model script inside the project; does not prove live model behavior",
        ).optional(),
        maxAttempts: positive.max(20).optional(),
        maxSteps: positive.max(100).optional(),
        runTimeout: z.number().positive().max(3600).optional(),
      })
      .strict(),
  },
  review: {
    description:
      "Read a bounded candidate diff from an InspectCJ run belonging to this project. Review before requesting explicit application.",
    schema: z.object({ run }).strict(),
  },
  apply: {
    description:
      "Apply a reviewed InspectCJ candidate after explicit host permission. The CLI checks source, candidate, and immutable acceptance hashes and refuses stale changes. This modifies project source.",
    schema: z.object({ run }).strict(),
  },
} satisfies Record<
  Operation,
  { description: string; schema: ReturnType<typeof z.object> }
>;

export const operations = Object.keys(definitions) as Operation[];
