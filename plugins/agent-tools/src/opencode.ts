import type { Plugin, ToolDefinition } from "@opencode-ai/plugin";
import {
  applicationTarget,
  executeOperation,
  type Arguments,
} from "./bridge.js";
import { definitions, operations } from "./tools.js";

const inspectCJ: Plugin = async (input) => ({
  tool: Object.fromEntries(
    operations.map((operation) => {
      const definition = definitions[operation];
      return [
        `inspect_cj_${operation}`,
        {
          description: definition.description,
          args: definition.schema.shape,
          async execute(raw: Record<string, unknown>, context) {
            const args = definition.schema.parse(raw) as Arguments;
            const cwd = context.directory || input.directory;
            let authorizedTarget: string | undefined;
            if (operation === "apply") {
              authorizedTarget = await applicationTarget(args.run!, cwd);
              await context.ask({
                permission: "edit",
                patterns: [authorizedTarget],
                always: [],
                metadata: { operation: "inspect_cj_apply", run: args.run },
              });
            }
            const result = await executeOperation(operation, args, {
              cwd,
              signal: context.abort,
              authorizedTarget,
            });
            return JSON.stringify(result);
          },
        } satisfies ToolDefinition,
      ];
    }),
  ),
});
export default inspectCJ;
