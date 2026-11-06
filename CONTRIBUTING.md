# Contributing

InspectCoder CJ separates its Python engine, shared executable resolver, and native clients.
Read GETTING_STARTED.md for supported SDK installation and docs/publishing.md for release packaging.

## Development setup

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
npm --prefix plugins/agent-tools ci --ignore-scripts
npm --prefix editors/vscode ci --ignore-scripts
```

Configure SDK paths through `inspectcoder-cj configure`; use a separate config directory for tests so personal settings remain untouched.
The optional `INSPECT_CJ_CONFIG` override selects a test-only config path.
No secrets or machine-specific paths belong in source or committed settings.

## Checks

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
node --test runtime/test/*.test.cjs
npm --prefix plugins/agent-tools run check
npm --prefix plugins/agent-tools test
plugins/agent-tools/node_modules/.bin/prettier --check runtime scripts/copy-runtime.cjs
npm --prefix editors/vscode test
npm --prefix editors/vscode run check
.venv/bin/python scripts/release.py check
```

Set `INSPECT_CJ_NATIVE=1` for Python native integration and `INSPECT_CJ` to your installed CLI for opt-in agent tests.
Run compiler/debugger suites serially.
Use `npm run test:native` and `npm run test:hosts` inside plugins/agent-tools with the documented external host test binaries.
Default suites explicitly skip native/host checks rather than pretending to have run them.

For VS Code integration tests, select disposable profile storage with `INSPECT_CJ_EDITOR_TEST_ROOT`, set `VSCODE_EXECUTABLE` to the editor binary and `INSPECT_CJ_TEST_CLI` to the installed CLI, then run `npm --prefix editors/vscode run test:host`.
Set `INSPECT_CJ_EXTENSION_ROOT` to an installed extension directory when verifying the packaged VSIX.
Keep the real `HOME` for editor launches because macOS credential services depend on it; isolate the SDK settings with `INSPECT_CJ_CONFIG` instead.
The test harness removes legacy toolchain overrides restored by the editor's login shell before checking the explicitly selected test configuration.

Reproduce bugs through the end-user interface before changing implementation.
Add behavioral regression tests, verify they fail before the fix and pass afterward, and keep independent acceptance immutable.
Review changes and run the relevant checks before submitting them.
Generated runtime copies, licenses copied into client packages, lockfiles, and release outputs must be produced by their respective tooling.
Do not manually edit generated delivery records or CHANGELOG files.

## Changes and releases

Keep package versions synchronized and preserve both CLI command names.
Change shared resolver behavior in runtime/cli.cjs and regenerate package copies through normal build scripts.
Use the issue tracker for a focused bug report or proposal; do not include tokens, private sources, or unsanitized model traces.
Release ownership and publication remain with maintainers.
