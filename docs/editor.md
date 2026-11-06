# VS Code setup and workflows

Install the Python engine and save SDK selection using [Getting started](../GETTING_STARTED.md), then ensure `inspectcoder-cj doctor` succeeds.
Install the local `inspectcoder-cj-0.2.0.vsix` through **Extensions: Install from VSIX**, or install `mtghorbani.inspectcoder-cj` after Marketplace publication.
The client discovers normal CLI installations without repository-specific exports.
Use `inspectcoder-cj configure --cli /absolute/path/to/inspectcoder-cj` for a custom directory or set `inspectCJ.executable` intentionally.

## Standalone repair

Open a saved `.cj` file in a trusted local folder.
Run **InspectCJ: Configure Repair Inputs** and enter independently known expected output and any argv/stdin or immutable driver.
Run **InspectCJ: Repair Current File**.
The extension reports progress and diagnostics and preserves the original source.
Use **InspectCJ: Review Candidate Diff**, inspect the diff, then **InspectCJ: Apply Candidate** when ready.
Changed source, modified assertions or drivers, and altered candidates are refused at application.
Do not infer semantic correctness from a compilation-only outcome.

## Native package and selected-test repair

Open the containing cjpm workspace so local packages, fixtures and dependencies remain inside its boundary.
Use **InspectCJ: Repair Package** for the full suite or **InspectCJ: Repair Selected Native Test** to focus debugger inspection.
Full-suite assertion acceptance remains required even when inspection is focused.
Native support is certified for macOS arm64 and SDK/cjpm/CJDB 1.1.0 only.
See [native behavior and limits](native.md).

## Settings and trust

The defaults use executable discovery and the shared saved user SDK settings.
Optional workspace settings retain `inspectCJ.executable`, child-only `inspectCJ.environment`, transport/model/script, request file, and timeout overrides.
A trusted workspace is required before executing its native code or configuration.
No credentials belong in workspace settings.
The extension refuses remote/browser, virtual, untrusted, unsaved, symlinked-source and Windows workflows as appropriate.
Cancellation stops the owned CLI; source changes remain separate from candidate artifacts.

## Build, test, package and uninstall

From the repository root:

```sh
npm --prefix editors/vscode ci --ignore-scripts
npm --prefix editors/vscode test
npm --prefix editors/vscode run check
npm --prefix editors/vscode run package -- --out ../../dist/inspectcoder-cj-0.2.0.vsix
```

The package build includes the generated shared resolver, MIT license, and attribution.
For all coordinated release artifacts and publisher checks, use [Publishing](publishing.md).
The existing extension-host runner requires your own absolute editor executable, disposable test directory, and installed CLI:

```sh
VSCODE_EXECUTABLE=/path/to/Code \
INSPECT_CJ_EDITOR_TEST_ROOT=/path/to/disposable/editor-test \
INSPECT_CJ_TEST_CLI=/path/to/inspectcoder-cj \
npm --prefix editors/vscode run test:host
```

These are test-run inputs, not mandatory end-user environment variables.
Run native editor tests serially with Python and agent native tests.
Uninstall with the Extensions view or `code --uninstall-extension mtghorbani.inspectcoder-cj`.
Removing the extension does not delete the shared CLI, saved SDK config, or repair artifacts.
