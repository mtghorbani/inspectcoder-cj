# InspectCoder CJ for OpenCode and Pi

Six native tools for Cangjie compilation, testing, CJDB debugging, evidence-grounded repair, candidate review, and guarded apply.
This single npm package provides both an OpenCode plugin and a Pi extension.

## Prerequisites

Install the Python engine, compatible SDK, and model transport using [Getting started](https://github.com/mtghorbani/inspectcoder-cj/blob/main/GETTING_STARTED.md).
The clients do not bundle a Python interpreter or SDK.
Node.js 22+ is required.
The tested host versions are OpenCode 1.19.5 and Pi 1.5.0.
Native package certification remains macOS arm64 with Cangjie 1.1.0 and a compatible Apple SDK.

```sh
uv tool install --python python3.11 inspectcoder-cj==0.2.0
inspectcoder-cj configure --sdk "$HOME/Tools/cangjie"
inspectcoder-cj doctor
```

Those registry install commands apply after publication.
Before publication, use the local wheel and npm tarball from the release build.
CLI discovery uses an explicit client override, optional legacy `INSPECT_CJ`, saved user config, PATH, and normal user command directories.
No repository-specific virtual environment or required environment export is needed.
For a custom CLI installation, save its launcher with `inspectcoder-cj configure --cli /absolute/path/to/inspectcoder-cj`.
The engine reads the saved SDK settings independently, including a compatible explicit sysroot when necessary.

## OpenCode installation

After npm publication, add the versioned package to your `opencode.json` plugin list:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@mtghorbani/inspectcoder-cj@0.2.0"]
}
```

OpenCode installs npm plugins and their runtime dependencies at startup.
Restart OpenCode after changing the configuration.
For a local release tarball:

```sh
npm install --prefix ./inspectcoder-client ./mtghorbani-inspectcoder-cj-0.2.0.tgz
```

Use the installed `node_modules/@mtghorbani/inspectcoder-cj/dist/opencode.js` file URL as the plugin entry.
A file URL starts with `file:///`; encode spaces as `%20`.
The package default export is the OpenCode plugin.

## Pi installation

After npm publication:

```sh
pi install npm:@mtghorbani/inspectcoder-cj@0.2.0
```

Pi's package manifest loads `dist/pi.js`.
The `pi-package` keyword enables gallery discovery; it does not create a separate proprietary package format.
Before publication, install the local tarball with npm as shown above, then register the installed directory:

```sh
pi install ./inspectcoder-client/node_modules/@mtghorbani/inspectcoder-cj
```

Restart Pi or run `/reload` after changes.
The extension does not bundle a duplicate host API.

## Tools and permissions

| Tool | Behavior |
| --- | --- |
| `inspect_cj_doctor` | Compile and debug a probe to check installed capabilities |
| `inspect_cj_test` | Execute a project.json contract or complete native cjpm suite |
| `inspect_cj_debug` | Run ordered actions in one persistent CJDB session and return real observations |
| `inspect_cj_repair` | Repair a private snapshot with independent expectations or native assertions |
| `inspect_cj_review` | Read a bounded regular candidate diff belonging to the current project |
| `inspect_cj_apply` | Ask host permission, then check source, candidate and acceptance hashes before writing |

Example standalone repair arguments:

```json
{
  "target": "main.cj",
  "task": "Print the inclusive sum from 1 through n",
  "expectStdout": "10\n",
  "args": ["4"]
}
```

For a native package, use `{"target":"path/to/package"}` and keep the existing assertions.
Use the returned `result.output` directory as the `run` argument for review/apply.
Repairs never overwrite original source.
`compile_only` proves compilation only; `needs_input` means the tool needs independent acceptance or target selection.
The default repair model transport is the authenticated Codex CLI, independently of the host conversation model.
Optional `transport`, `model`, and project-local deterministic `script` inputs select the existing engine transports.
Scripted transport is a testing mode and does not prove live model quality.

Targets, indirect manifests, containing native workspaces, and recorded apply inputs must remain inside the canonical host project.
Private artifacts may live in the CLI cache outside that project.
Open the containing workspace if a package belongs to a workspace above the current session directory.
OpenCode requests `edit` permission for the exact source.
Pi requires an interactive apply confirmation and refuses headless apply; use the CLI explicitly after reviewing in headless workflows.
The bridge enforces cancellation, deadlines, process-group cleanup, a 4 MiB combined process-output cap, and 1 MiB artifact reads.
Special-file artifacts and malformed configuration are refused.

## Development and publication

Build from a complete repository checkout:

```sh
npm ci --ignore-scripts
npm run check
npm test
npm run build
npm pack --dry-run
```

The repository build copies the shared CLI resolver and MIT attribution into the distributable package.
The packed runtime is self-contained JavaScript and does not reference files outside the installed package.
Host SDKs and tests are development-only and are excluded from the package.
The current upstream OpenCode development SDK has advisory-bearing dependencies that are not imported by the shipped runtime; the shipped dependency graph is audited separately.
See [Publishing](https://github.com/mtghorbani/inspectcoder-cj/blob/main/docs/publishing.md) for identity checks, npm authentication, local build verification, and the publication command.
No publish command runs during installation, build, or tests.
