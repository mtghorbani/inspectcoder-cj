import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, writeFile, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { once } from "node:events";
import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
} from "@earendil-works/pi-coding-agent";

// A deterministic provider drives the real host tool loop, without provider credentials.
async function modelServer() {
  let sawNeedsInput = false;
  const server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const request = JSON.parse(body);
    const messages = request.messages ?? [];
    if (
      messages.some(
        (m: any) =>
          m.role === "tool" &&
          JSON.stringify(m.content).includes("needs_input"),
      )
    )
      sawNeedsInput = true;
    const hasResult = messages.some((m: any) => m.role === "tool");
    const canCall = request.tools?.some(
      (t: any) => t.function?.name === "inspect_cj_repair",
    );
    const call = !hasResult && canCall;
    res.writeHead(200, { "content-type": "text/event-stream" });
    const chunk = (delta: unknown, finish: unknown = null) =>
      res.write(
        `data: ${JSON.stringify({ id: "completion", object: "chat.completion.chunk", created: 1, model: "test", choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`,
      );
    if (call) {
      chunk({
        role: "assistant",
        tool_calls: [
          {
            index: 0,
            id: "call_inspect",
            type: "function",
            function: {
              name: "inspect_cj_repair",
              arguments: JSON.stringify({ target: "main.cj" }),
            },
          },
        ],
      });
      chunk({}, "tool_calls");
    } else {
      chunk({
        role: "assistant",
        content: "Finished checking the InspectCJ tool.",
      });
      chunk({}, "stop");
    }
    res.end("data: [DONE]\n\n");
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address() as { port: number };
  return {
    server,
    baseURL: `http://127.0.0.1:${address.port}/v1`,
    sawNeedsInput: () => sawNeedsInput,
  };
}

async function project() {
  const cwd = await realpath(
    await mkdtemp(path.join(tmpdir(), "inspect-real-host-")),
  );
  await writeFile(path.join(cwd, "main.cj"), 'main() { println("wrong"); 0 }');
  return cwd;
}

const enabled = process.env.INSPECT_CJ_HOST_TESTS === "1";

test(
  "OpenCode server discovers the packaged plugin and executes a real tool call",
  { skip: !enabled, timeout: 120000 },
  async () => {
    const cwd = await project();
    const model = await modelServer();
    const profile = path.join(cwd, "profile");
    await mkdir(profile);
    await writeFile(
      path.join(cwd, "opencode.json"),
      JSON.stringify({
        plugin: [pathToFileURL(path.resolve("dist/opencode.js")).href],
        model: "inspect-test/test",
        provider: {
          "inspect-test": {
            npm: "@ai-sdk/openai-compatible",
            models: {
              test: { name: "test", limit: { context: 8192, output: 512 } },
            },
            options: { baseURL: model.baseURL, apiKey: "test-only" },
          },
        },
        permission: { inspect_cj_repair: "allow" },
      }),
    );
    const child = spawn(
      process.env.OPENCODE_BIN ?? "opencode",
      ["serve", "--port", "0", "--hostname", "127.0.0.1"],
      {
        cwd,
        env: {
          ...process.env,
          XDG_CONFIG_HOME: path.join(profile, "config"),
          XDG_DATA_HOME: path.join(profile, "data"),
          XDG_STATE_HOME: path.join(profile, "state"),
          XDG_CACHE_HOME: path.join(profile, "cache"),
          OPENCODE_DISABLE_DEFAULT_PLUGINS: "true",
        },
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let output = "";
    child.stdout.on("data", (c) => (output += c));
    child.stderr.on("data", (c) => (output += c));
    try {
      const deadline = Date.now() + 90000;
      let url: string | undefined;
      while (Date.now() < deadline && !url) {
        url = output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
        if (child.exitCode !== null) throw new Error(output);
        if (!url) await new Promise((r) => setTimeout(r, 100));
      }
      assert.ok(url, output);
      const ids = (await (
        await fetch(`${url}/experimental/tool/ids`)
      ).json()) as string[];
      assert.ok(ids.includes("inspect_cj_repair"), JSON.stringify(ids));
      const session = (await (
        await fetch(`${url}/session`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: "{}",
        })
      ).json()) as { id: string };
      const response = await fetch(`${url}/session/${session.id}/message`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          model: { providerID: "inspect-test", modelID: "test" },
          parts: [
            {
              type: "text",
              text: "Check the repair tool for main.cj without inventing an expected result.",
            },
          ],
        }),
      });
      const result = await response.json();
      assert.equal(response.status, 200, JSON.stringify(result));
      assert.equal(model.sawNeedsInput(), true, JSON.stringify(result));
    } finally {
      if (child.pid) {
        try {
          process.kill(-child.pid, "SIGTERM");
        } catch {}
      }
      model.server.closeAllConnections();
      model.server.close();
    }
  },
);

test(
  "Pi loads the built extension and executes a real agent tool loop",
  { skip: !enabled, timeout: 60000 },
  async () => {
    const cwd = await project();
    const agentDir = path.join(cwd, "agent");
    await mkdir(agentDir);
    const model = await modelServer();
    let session:
      | Awaited<ReturnType<typeof createAgentSession>>["session"]
      | undefined;
    try {
      await writeFile(
        path.join(agentDir, "models.json"),
        JSON.stringify({
          providers: {
            "inspect-test": {
              baseUrl: model.baseURL,
              api: "openai-completions",
              apiKey: "test-only",
              models: [
                {
                  id: "test",
                  name: "test",
                  reasoning: false,
                  input: ["text"],
                  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
                  contextWindow: 8192,
                  maxTokens: 512,
                },
              ],
            },
          },
        }),
      );
      const resourceLoader = new DefaultResourceLoader({
        cwd,
        agentDir,
        additionalExtensionPaths: [path.resolve("dist/pi.js")],
        noSkills: true,
        noPromptTemplates: true,
        noThemes: true,
        noContextFiles: true,
      });
      await resourceLoader.reload();
      assert.deepEqual(resourceLoader.getExtensions().errors, []);
      const modelRuntime = await ModelRuntime.create({
        modelsPath: path.join(agentDir, "models.json"),
        authPath: path.join(agentDir, "auth.json"),
        allowModelNetwork: false,
      });
      const selected = modelRuntime.getModel("inspect-test", "test");
      assert.ok(selected, modelRuntime.getError());
      ({ session } = await createAgentSession({
        cwd,
        agentDir,
        resourceLoader,
        modelRuntime,
        model: selected,
        thinkingLevel: "off",
        tools: ["inspect_cj_repair"],
        sessionManager: SessionManager.inMemory(cwd),
      }));
      assert.ok(
        session.getAllTools().some((t) => t.name === "inspect_cj_repair"),
      );
      await session.prompt(
        "Check main.cj using the repair tool without guessing expected output.",
      );
      assert.equal(model.sawNeedsInput(), true);
    } finally {
      session?.dispose();
      model.server.closeAllConnections();
      model.server.close();
    }
  },
);
