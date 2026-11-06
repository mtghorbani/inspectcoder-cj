import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { TSchema } from "typebox";
import { z } from "zod";
import {
  applicationTarget,
  executeOperation,
  type Arguments,
} from "./bridge.js";
import { definitions, operations } from "./tools.js";

export default function inspectCJ(pi: ExtensionAPI) {
  for (const operation of operations) {
    const definition = definitions[operation];
    pi.registerTool({
      name: `inspect_cj_${operation}`,
      label: `InspectCJ ${operation}`,
      description: definition.description,
      // Both hosts consume the same declarative contract; execution revalidates it.
      parameters: z.toJSONSchema(definition.schema, {
        target: "draft-7",
      }) as TSchema,
      async execute(_id, raw, signal, _onUpdate, context) {
        const args = definition.schema.parse(raw) as Arguments;
        let authorizedTarget: string | undefined;
        if (operation === "apply") {
          if (!context.hasUI)
            throw new Error(
              "Apply requires interactive user confirmation; use inspect-cj apply manually in headless sessions.",
            );
          authorizedTarget = await applicationTarget(args.run!, context.cwd);
          const accepted = await context.ui.confirm(
            "Apply InspectCJ candidate?",
            `Replace ${authorizedTarget} with the reviewed candidate from ${args.run}? Source and acceptance hashes will be checked.`,
            { signal },
          );
          if (!accepted) throw new Error("Application declined");
        }
        const outcome = await executeOperation(operation, args, {
          cwd: context.cwd,
          signal,
          authorizedTarget,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(outcome) }],
          details: outcome,
        };
      },
    });
  }
}
