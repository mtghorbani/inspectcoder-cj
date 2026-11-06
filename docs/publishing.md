# Publishing InspectCoder CJ

This repository prepares release artifacts locally and never publishes as a build or test side effect.
The current coordinated version is 0.2.0.
The identities supplied by the maintainer are:

| Purpose | Value | What it controls |
| --- | --- | --- |
| Source repository | `https://github.com/mtghorbani/inspectcoder-cj` | Source, issues, documentation and release downloads, subject to repository access |
| VS Code publisher | `mtghorbani` | Marketplace publishing profile owned by the maintainer |
| VS Code extension name | `inspectcoder-cj` | Combined install identifier `mtghorbani.inspectcoder-cj` |
| npm package | `@mtghorbani/inspectcoder-cj` | One public package consumed by both OpenCode and Pi |
| Python distribution | `inspectcoder-cj` | Shared CLI engine installed from PyPI |
| License | MIT | Redistribution grant for this original implementation |

These are public names, not authentication credentials.
Choosing them does not create accounts, reserve names, verify ownership, or upload anything.
Before publishing, create or verify the matching accounts and confirm those names are available to you.
If the npm account or organization is not named `mtghorbani`, use a scope you own and update the npm manifest and release.json together.
If PyPI's unscoped name is unavailable, select an available distribution name and update pyproject.toml and release.json together.
The two CLI command names can remain stable when the Python distribution name changes.

## 1. Prepare accounts and source repository

The initial GitHub repository must be private and contain a clean source snapshot on `main`, without the local development history.
Inspect local changes and commit the source you intend to distribute, then export that exact reviewed commit with `git archive`.
The committed `.gitattributes` excludes local delivery records, datasets, future plans and personal editor settings from that export.
Inspect the archive before upload: source, tests, documentation, examples, licenses, CI and native clients must remain complete, while credentials, SDKs, virtual environments, build outputs, node_modules and internal delivery data must be absent.
Initialize a separate repository from the reviewed export and upload only its new snapshot commit to the private repository.
Do not push this development repository's history or change its remote as a shortcut.
Preserve all existing local commits and data; archive exclusions do not remove files from that history.
Verify the destination is private and its `main` contains only the intended snapshot, then verify hosted CI.
Repository creation and upload are separate maintainer actions, never side effects of release.py.
Do not force-push an existing repository or change its visibility without an explicit decision.

Create the VS Code publisher profile `mtghorbani` through the [official Marketplace publisher guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension).
The publisher field is `mtghorbani`, not the full extension ID.
Create or verify the npm account/organization owning `@mtghorbani` and a PyPI account permitted to publish `inspectcoder-cj`.
Enable the authentication protections recommended by each service.
Keep tokens in your credential manager or CI secret store, never release.json, package manifests, or chat.

## 2. Install build dependencies

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
npm --prefix plugins/agent-tools ci --ignore-scripts
npm --prefix editors/vscode ci --ignore-scripts
```

Python runtime dependencies are empty; build/test tools and host SDK types are development-only.
The engine's SDK/model prerequisites are installed and authenticated separately as described in GETTING_STARTED.md.
Cangjie, Apple SDKs, Python interpreters, and credentials are not bundled or redistributed.

## 3. Validate source and build local packages

```sh
.venv/bin/python -m pytest -q
node --test runtime/test/*.test.cjs
npm --prefix plugins/agent-tools run check
npm --prefix plugins/agent-tools test
npm --prefix editors/vscode run check
npm --prefix editors/vscode test
.venv/bin/python scripts/release.py check
.venv/bin/python scripts/release.py build
```

The preflight checks matching versions, selected identities, repository metadata, and MIT licensing.
Invalid credentials in a repository URL, placeholder IDs, unknown release-config fields, and mismatched metadata stop preparation.
Build runs from a disposable staged source copy and includes the shared executable resolver, license and attribution in each client package.
It generates and inspects these files under `dist/release/0.2.0`:

- `inspectcoder_cj-0.2.0-py3-none-any.whl`
- `inspectcoder_cj-0.2.0.tar.gz`
- `mtghorbani-inspectcoder-cj-0.2.0.tgz`
- `inspectcoder-cj-0.2.0.vsix`
- `SHA256SUMS`
- `release-manifest.json`

The manifest records package identities and hashes and explicitly states that publication was not performed.
The output directory must be empty; use `--output /another/empty/directory` for another build without overwriting prior artifacts.
Python's build frontend may create an isolated build environment and fetch the declared build backend.
No package is uploaded and no publisher authentication is requested during build.

Run native tests serially using a separate configured profile before a production release.
Use the real supported SDK, actual OpenCode/Pi loops and VS Code extension-host checks rather than treating opt-in skips as passes.
For changes to executable discovery, verify a new user profile with legacy toolchain variables unset and an actual installed wheel.
The CI workflow performs portable tests and package builds; generic hosted runners do not certify the native Cangjie environment.

## 4. Install the exact local artifacts

```sh
uv tool install --python python3.11 ./dist/release/0.2.0/inspectcoder_cj-0.2.0-py3-none-any.whl
inspectcoder-cj configure --sdk "$HOME/Tools/cangjie"
inspectcoder-cj doctor
code --install-extension ./dist/release/0.2.0/inspectcoder-cj-0.2.0.vsix
npm install --prefix ./client-preview ./dist/release/0.2.0/mtghorbani-inspectcoder-cj-0.2.0.tgz
pi install ./client-preview/node_modules/@mtghorbani/inspectcoder-cj
```

For OpenCode before npm publication, use a file URL pointing to `client-preview/node_modules/@mtghorbani/inspectcoder-cj/dist/opencode.js`.
The npm tarball's default export is OpenCode, while its `pi.extensions` manifest selects Pi's entry point.
An npm package install does not install a second host API, run native code, download an SDK, or provision Python.
The normal clients discover the installed CLI and load saved SDK configuration.

## 5. Publish the shared Python engine

Only run this step when you intentionally want a public release.
Create a PyPI API token or configure PyPI's trusted-publisher flow according to [PyPA's publishing guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/).
Use the local credential prompt or a protected credential store; do not put a token into shell history.

```sh
.venv/bin/python -m twine check dist/release/0.2.0/inspectcoder_cj-0.2.0*
.venv/bin/python -m twine upload \
  dist/release/0.2.0/inspectcoder_cj-0.2.0-py3-none-any.whl \
  dist/release/0.2.0/inspectcoder_cj-0.2.0.tar.gz
```

PyPI release files cannot be silently replaced in place.
Choose a new version for corrected release bytes rather than trying to reuse an uploaded filename/version.
After publication, verify in a fresh tool environment with `uv tool install inspectcoder-cj==0.2.0`.
Publish the engine before announcing clients that require it.

## 6. Publish npm for both Pi and OpenCode

Authenticate to the account that owns the scope and verify the intended identity:

```sh
npm login
npm whoami
npm publish ./dist/release/0.2.0/mtghorbani-inspectcoder-cj-0.2.0.tgz --access public
```

The explicit public-access flag is required for this intended scoped public release.
Follow current [npm scoped-public-package guidance](https://docs.npmjs.com/creating-and-publishing-scoped-public-packages/) for account verification and authentication requirements.
The package metadata also declares `publishConfig.access` as public.

Users then install Pi with:

```sh
pi install npm:@mtghorbani/inspectcoder-cj@0.2.0
```

OpenCode users add `"@mtghorbani/inspectcoder-cj@0.2.0"` to their `opencode.json` plugin list.
OpenCode's [official plugin guide](https://opencode.ai/docs/plugins/) documents npm installation and ecosystem visibility.
Pi's [package guide](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/packages.md) documents the `pi-package` keyword and package gallery eligibility.
There is no second binary marketplace submission format implemented by this project for either host.
A community listing or gallery appearance is a visibility step, not proof that a new package has been installed and tested.

## 7. Publish the VS Code package

Create the `mtghorbani` publisher profile first and authenticate with the credential method supported by your installed vsce and Marketplace account.
The [official publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension) explains publisher creation, token permissions and release constraints.

```sh
cd editors/vscode
npx vsce login mtghorbani
npx vsce publish --packagePath ../../dist/release/0.2.0/inspectcoder-cj-0.2.0.vsix
```

This uploads the already built VSIX rather than silently packaging a different working tree.
Users can then install `mtghorbani.inspectcoder-cj`.
An Open VSX listing is optional and requires its own account, namespace rights and publication procedure; this repository does not claim that distribution has happened.

## 8. Verify and announce

After intentional publication, install pinned versions into clean environments, configure your own SDK, run doctor, and exercise repair/review/guarded apply in each host.
Upload the matching archives and SHA256SUMS to a GitHub release if you choose that distribution channel.
Keep package versions synchronized and document the certified platform/SDK and known limitations.
Release CI is deliberately build-only and has no publish credentials or automatic upload job.
