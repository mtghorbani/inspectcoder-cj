import { spawn } from "node:child_process";
import { constants } from "node:fs";
import { open, realpath } from "node:fs/promises";
import path from "node:path";
import { resolveCli } from "../runtime/cli.cjs";

export type Operation =
  | "doctor"
  | "test"
  | "debug"
  | "repair"
  | "review"
  | "apply";
export interface Context {
  cwd: string;
  executable?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
  maxOutputBytes?: number;
  authorizedTarget?: string;
}
export interface Arguments {
  target?: string;
  run?: string;
  task?: string;
  driver?: string;
  stdinFile?: string;
  expectStdout?: string;
  expectStderr?: string;
  expectExit?: number;
  expectLine?: string[];
  args?: string[];
  compileOnly?: boolean;
  editable?: string;
  case?: string;
  transport?: string;
  model?: string;
  script?: string;
  maxAttempts?: number;
  maxSteps?: number;
  runTimeout?: number;
  actions?: Record<string, unknown>[];
}
export interface Outcome {
  code: number | null;
  result: Record<string, unknown>;
  stderr: string;
}

export async function projectPath(value: string, cwd: string): Promise<string> {
  const root = await realpath(cwd);
  const requested = path.resolve(root, value);
  const lexical = path.relative(root, requested);
  if (
    lexical === ".." ||
    lexical.startsWith(`..${path.sep}`) ||
    path.isAbsolute(lexical)
  ) {
    throw new Error("Path must be inside the current project");
  }
  const target = await realpath(requested);
  const relative = path.relative(root, target);
  if (
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    throw new Error("Path must be inside the current project");
  }
  return target;
}

async function readBounded(file: string, limit = 1024 * 1024): Promise<string> {
  // A FIFO must not block open before the descriptor can be type-checked.
  const handle = await open(file, constants.O_RDONLY | constants.O_NONBLOCK);
  try {
    if (!(await handle.stat()).isFile())
      throw new Error("Artifact must be a regular file");
    const buffer = Buffer.alloc(limit + 1);
    let total = 0;
    while (total < buffer.length) {
      const { bytesRead } = await handle.read(
        buffer,
        total,
        buffer.length - total,
        null,
      );
      if (!bytesRead) break;
      total += bytesRead;
    }
    if (total > limit) throw new Error("Artifact exceeds output limit");
    return buffer.subarray(0, total).toString("utf8");
  } finally {
    await handle.close();
  }
}

export async function validateRun(run: string, cwd: string): Promise<string> {
  const directory = await realpath(path.resolve(cwd, run));
  const invocation = JSON.parse(
    await readBounded(path.join(directory, "invocation.json")),
  );
  if (typeof invocation.root !== "string")
    throw new Error("Missing recorded project root");
  await projectPath(invocation.root, cwd);
  return directory;
}

export async function reviewRun(run: string, cwd: string) {
  const directory = await validateRun(run, cwd);
  const diffPath = await realpath(path.join(directory, "candidate.diff"));
  if (path.dirname(diffPath) !== directory)
    throw new Error("Candidate diff must be inside run");
  return {
    status: "review",
    run: directory,
    diff: await readBounded(diffPath),
    next: "Review the diff and request explicit application. Repair never changes original source.",
  };
}

export async function applicationTarget(
  run: string,
  cwd: string,
): Promise<string> {
  const directory = await validateRun(run, cwd);
  const record = JSON.parse(
    await readBounded(path.join(directory, "invocation.json")),
  );
  if (typeof record.config?.editable !== "string")
    throw new Error("Missing recorded editable source");
  return projectPath(path.resolve(record.root, record.config.editable), cwd);
}

export async function executeOperation(
  operation: Operation,
  args: Arguments,
  ctx: Context,
): Promise<Outcome> {
  if (ctx.signal?.aborted) throw new Error("InspectCJ cancelled before launch");
  if (operation === "review") {
    if (!args.run) throw new Error("run is required");
    return { code: 0, result: await reviewRun(args.run, ctx.cwd), stderr: "" };
  }
  if (!["darwin", "linux"].includes(process.platform))
    throw new Error("InspectCJ requires a POSIX host");
  const argv: string[] = [operation];
  if (operation === "apply") {
    if (!args.run) throw new Error("run is required");
    if (
      !ctx.authorizedTarget ||
      (await applicationTarget(args.run, ctx.cwd)) !== ctx.authorizedTarget
    ) {
      throw new Error(
        "Apply requires host permission for the current candidate target",
      );
    }
    argv.push(await validateRun(args.run, ctx.cwd));
  } else if (operation !== "doctor") {
    if (!args.target) throw new Error("target is required");
    argv.push(await projectPath(args.target, ctx.cwd));
  }
  let input = "";
  if (operation === "repair") {
    argv.push("--non-interactive");
    const strings = {
      task: "--task",
      expectStdout: "--expect-stdout",
      expectStderr: "--expect-stderr",
      editable: "--editable",
      case: "--case",
      transport: "--transport",
      model: "--model",
    } as const;
    for (const [key, flag] of Object.entries(strings)) {
      const value = args[key as keyof typeof strings];
      if (value !== undefined) {
        if (value === "-" && key === "task")
          throw new Error("task must be literal text, not stdin");
        argv.push(`${flag}=${value}`);
      }
    }
    for (const [key, flag] of Object.entries({
      driver: "--driver",
      stdinFile: "--stdin-file",
      script: "--script",
    })) {
      const value = args[key as "driver" | "stdinFile" | "script"];
      if (value !== undefined)
        argv.push(`${flag}=${await projectPath(value, ctx.cwd)}`);
    }
    for (const [key, flag] of Object.entries({
      expectExit: "--expect-exit",
      maxAttempts: "--max-attempts",
      maxSteps: "--max-steps",
      runTimeout: "--run-timeout",
    })) {
      const value =
        args[key as "expectExit" | "maxAttempts" | "maxSteps" | "runTimeout"];
      if (value !== undefined) {
        if (
          !Number.isFinite(value) ||
          (key !== "expectExit" && value <= 0) ||
          (key !== "runTimeout" && !Number.isInteger(value))
        )
          throw new Error(`Invalid ${key}`);
        argv.push(`${flag}=${value}`);
      }
    }
    for (const line of args.expectLine ?? [])
      argv.push(`--expect-line=${line}`);
    if (args.compileOnly) argv.push("--compile-only");
    if (args.args) argv.push("--", ...args.args);
  }
  if (operation === "debug") {
    if (!args.actions?.length || args.actions.length > 100)
      throw new Error("Provide 1 to 100 debugger actions");
    input =
      args.actions.map((action) => JSON.stringify(action)).join("\n") + "\n";
    if (Buffer.byteLength(input) > 65536)
      throw new Error("Debugger actions exceed 64KiB");
    if (args.case) argv.push(`--case=${args.case}`);
  }
  const deadline = ctx.timeoutMs ?? ((args.runTimeout ?? 600) + 60) * 1000;
  const completed = await runProcess(
    resolveCli({ executable: ctx.executable }),
    argv,
    input,
    ctx,
    deadline,
  );
  try {
    if (operation === "debug") {
      const observations = completed.stdout
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line));
      if (!observations.length)
        throw new Error("Missing debugger observations");
      return {
        code: completed.code,
        stderr: completed.stderr,
        result: {
          status: completed.code === 0 ? "success" : "failed",
          observations,
        },
      };
    }
    const result: unknown = JSON.parse(completed.stdout);
    if (
      !result ||
      typeof result !== "object" ||
      !("status" in result) ||
      typeof result.status !== "string"
    )
      throw new Error("Missing CLI status");
    return {
      code: completed.code,
      stderr: completed.stderr,
      result: result as Record<string, unknown>,
    };
  } catch (error) {
    throw new Error(
      `Invalid CLI JSON (exit ${completed.code}): ${error instanceof Error ? error.message : "invalid response"}`,
    );
  }
}

async function runProcess(
  executable: string,
  argv: string[],
  input: string,
  ctx: Context,
  timeoutMs: number,
): Promise<{ code: number | null; stdout: string; stderr: string }> {
  const cwd = await realpath(ctx.cwd);
  return new Promise((resolve, reject) => {
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0)
      return reject(new Error("Invalid timeout"));
    if (ctx.signal?.aborted)
      return reject(new Error("InspectCJ cancelled before launch"));
    const child = spawn(executable, argv, {
      cwd,
      env: { ...process.env, INSPECT_CJ_WORKSPACE_ROOT: cwd },
      shell: false,
      detached: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout: Buffer[] = [],
      stderr: Buffer[] = [];
    let size = 0;
    let failure: Error | undefined;
    let escalation: NodeJS.Timeout | undefined;
    const kill = (signal: NodeJS.Signals) => {
      if (!child.pid) return;
      try {
        process.kill(-child.pid, signal);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ESRCH")
          failure ??= error as Error;
      }
    };
    const stop = (message: string) => {
      failure ??= new Error(message);
      kill("SIGINT");
      escalation ??= setTimeout(() => kill("SIGKILL"), 1000);
    };
    const abort = () => stop("InspectCJ cancelled");
    ctx.signal?.addEventListener("abort", abort, { once: true });
    if (ctx.signal?.aborted) abort();
    const timeout = setTimeout(() => stop("InspectCJ timed out"), timeoutMs);
    for (const [stream, chunks] of [
      [child.stdout, stdout],
      [child.stderr, stderr],
    ] as const) {
      stream.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > (ctx.maxOutputBytes ?? 4 * 1024 * 1024))
          stop("CLI output exceeds limit");
        else chunks.push(chunk);
      });
    }
    child.stdin.on("error", (error: NodeJS.ErrnoException) => {
      if (error.code !== "EPIPE") failure ??= error;
    });
    child.on("error", (error) => {
      failure = new Error(
        `Cannot launch InspectCJ: ${error.message}. Set INSPECT_CJ to the installed CLI path.`,
      );
    });
    child.on("exit", () => kill("SIGKILL"));
    child.on("close", (code) => {
      clearTimeout(timeout);
      clearTimeout(escalation);
      ctx.signal?.removeEventListener("abort", abort);
      // Descendants cannot outlive this invocation even if the CLI exits first.
      kill("SIGKILL");
      if (failure) reject(failure);
      else
        resolve({
          code,
          stdout: Buffer.concat(stdout).toString("utf8"),
          stderr: Buffer.concat(stderr).toString("utf8"),
        });
    });
    child.stdin.end(input);
  });
}
