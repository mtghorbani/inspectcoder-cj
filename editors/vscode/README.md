# InspectCoder CJ

Repair Cangjie programs using real compiler diagnostics, CJDB observations, and existing native assertions.
Candidates are validated in a private snapshot and presented as a diff before any application.

## Install and configure

Install this extension from a local VSIX before publication, or as `mtghorbani.inspectcoder-cj` from the VS Code Marketplace after publication.
Then install the separately distributed Python engine and compatible Cangjie SDK using [Getting started](https://github.com/mtghorbani/inspectcoder-cj/blob/main/GETTING_STARTED.md).

```sh
uv tool install --python python3.11 inspectcoder-cj==0.2.0
inspectcoder-cj configure --sdk "$HOME/Tools/cangjie"
inspectcoder-cj doctor
```

The registry command applies after the Python package is published; local wheels work before publication.
The CLI, SDK, and default model transport must be installed and authenticated separately.
No author-specific shell exports or checkout virtual environment are required.
CLI discovery supports saved user configuration, PATH, and normal user installation directories even when the GUI PATH is sparse.
For a custom launcher, save `inspectcoder-cj configure --cli /absolute/path/to/inspectcoder-cj` or set the extension's executable setting.

## Use

Open a saved `.cj` file in a trusted local workspace.
Use the command palette:

- **InspectCJ: Configure Repair Inputs** to supply independently expected behavior for standalone code.
- **InspectCJ: Repair Current File** for standalone or function-driver repair.
- **InspectCJ: Repair Package** to use immutable native assertions.
- **InspectCJ: Repair Selected Native Test** to focus inspection while retaining full-suite acceptance.
- **InspectCJ: Review Candidate Diff** to inspect the candidate.
- **InspectCJ: Apply Candidate** to explicitly apply after hash checks and confirmation.
- **InspectCJ: Cancel Repair** to stop the owned CLI operation.

The established InspectCJ command labels are retained for compatibility.
See [the complete editor guide](https://github.com/mtghorbani/inspectcoder-cj/blob/main/docs/editor.md).

## Requirements and privacy

The certified native package environment is macOS arm64 with Cangjie 1.1.0 and a compatible Apple SDK.
The extension requires a local POSIX host; remote, browser, virtual, untrusted, and Windows workspaces are refused.
Other compiler/platform combinations do not inherit native certification.
The default repair transport invokes your authenticated Codex CLI and sends the declared repair context to that provider.
The extension does not install an SDK, change account credentials, or collect its own telemetry.
Model charges and provider data policies depend on your chosen transport.
Original source remains unchanged until explicit application; immutable assertions and stale-source checks are preserved.

## Settings

`inspectCJ.executable` selects an explicit CLI when discovery is unsuitable.
`inspectCJ.environment` provides optional child-only automation overrides.
`inspectCJ.transport`, `inspectCJ.model`, `inspectCJ.script`, and `inspectCJ.runTimeout` control the existing repair workflow.
Prefer the user's `inspectcoder-cj configure` settings for persistent SDK selection.
Never put credentials into checked-in workspace settings.

## License and support

MIT licensed, with research attribution in NOTICE.md.
Report reproducible issues at [GitHub Issues](https://github.com/mtghorbani/inspectcoder-cj/issues).
For security concerns use the repository's [security policy](https://github.com/mtghorbani/inspectcoder-cj/blob/main/SECURITY.md).
