# Getting started with InspectCoder CJ

This guide installs the shared engine once and connects any supported client to it.
No personal installation paths or required shell exports are built into the packages.

## 1. Install prerequisites

The verified native package environment is macOS arm64, Cangjie SDK 1.1.0, CJDB from that same SDK, and an Apple SDK compatible with its linker.
Python 3.11 or newer runs the CLI; the verified CJDB embeds Python 3.11 and may need its framework installed separately.
Use Node.js 22+ for Pi/OpenCode and for building the JavaScript clients.

1. Install Python 3.11 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
   On a Homebrew-equipped Mac, `brew install python@3.11 uv libffi` installs the relevant prerequisites without selecting an InspectCoder SDK path.
2. Install Apple's command-line developer tools with `xcode-select --install`, or install Xcode from Apple.
3. Download the SDK for your architecture from the [official Cangjie download center](https://cangjie-lang.cn/en/download), selecting the certified 1.1.0 release for native cjpm workflows.
   Extract it somewhere you control, for example `~/Tools/cangjie`.
   The selected directory must contain `bin/cjc`, `tools/bin/cjpm`, and `third_party/llvm/bin/lldb`.
4. Install and authenticate the [Codex CLI](https://developers.openai.com/codex/cli/) for the default repair transport, following [official authentication instructions](https://developers.openai.com/codex/auth/).
   `codex login` and `codex login status` are sufficient for the normal authenticated CLI flow.
   Do not put provider credentials in repository configuration.

The packages do not redistribute Python, the Cangjie SDK, Apple SDKs, or model credentials.
The vendor's SDK installation requirements still apply.
The native adapter rejects unsupported compiler/package-manager versions instead of silently claiming support.

## 2. Install the Python engine

Before marketplace publication, download the built wheel from the maintainer or build it with the publishing guide.
Install that local file:

```sh
uv tool install --python python3.11 ./inspectcoder_cj-0.2.0-py3-none-any.whl
```

After publication to PyPI:

```sh
uv tool install --python python3.11 inspectcoder-cj==0.2.0
```

`pipx install --python python3.11 ./inspectcoder_cj-0.2.0-py3-none-any.whl` is an alternative.
These installers create an isolated Python environment and normal user command launchers.
If your terminal cannot find `inspectcoder-cj`, follow uv's reported executable-directory guidance or run `uv tool update-shell` and open a new terminal.
GUI clients also search normal user CLI directories, so they do not depend on a repository-specific `INSPECT_CJ` export.
Keep the CLI installation when uninstalling a client if another client still uses it.

## 3. Save SDK selection and check it

```sh
inspectcoder-cj configure --sdk "$HOME/Tools/cangjie"
inspectcoder-cj configure
inspectcoder-cj doctor
```

`configure` saves absolute paths in `~/.config/inspect-cj/config.json`, or `$XDG_CONFIG_HOME/inspect-cj/config.json` when that standard config directory is selected.
It does not modify shell startup files or system SDK defaults.
With no arguments it displays the selected configuration file and saved settings.
The `inspect-cj` command is a compatibility alias.

On macOS, automatic sysroot selection uses `xcrun --sdk macosx --show-sdk-path`.
A newer installed Apple SDK is not necessarily compatible with the older Cangjie 1.1 linker.
The verified environment used MacOSX15.4.sdk; the current Apple SDK can fail with incompatible text-stub or arm64 linker errors.
Install a compatible Apple SDK through Apple's developer tools and save its actual location:

```sh
inspectcoder-cj configure --sysroot /path/to/compatible/MacOSX15.4.sdk
inspectcoder-cj doctor
```

The path above is a placeholder for your installation, not a file included in this repository.
Do not copy an SDK from another user's machine or replace system defaults to match an example.
If CJDB cannot load its Python framework, ensure `python3.11` is installed and discoverable, or save its framework prefix:

```sh
inspectcoder-cj configure --python-framework /path/to/Frameworks
```

`doctor` compiles and runs a probe, stops at a native breakpoint, and inspects a real variable.
Fix a failed doctor before attempting repair.

## 4. Connect a client

All three clients require the Python engine and SDK setup above.
Installing a client alone does not install or authenticate a model or SDK.

Install whichever host applications you want to use before adding their plugins:

- **VS Code:** use the [official desktop download](https://code.visualstudio.com/download).
- **OpenCode:** follow the [official OpenCode 1 installation guide](https://opencode.ai/docs/#install).
- **Pi:** follow the [official quick start](https://github.com/earendil-works/pi/tree/main/packages/coding-agent#quick-start).

For the terminal host versions verified with this release, install with Node.js 22+:

```sh
npm install -g opencode-ai@1.19.5
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@1.5.0
```

Follow the selected host's provider-login instructions before using its agent conversation.
The shared engine's repair transport still uses the separate authentication from step 1.

**VS Code:** before publication, use **Extensions: Install from VSIX** with `inspectcoder-cj-0.2.0.vsix`.
After publication, install `mtghorbani.inspectcoder-cj` from the Marketplace or run `code --install-extension mtghorbani.inspectcoder-cj`.
Open a trusted, saved local workspace and use **InspectCJ: Repair Current File** or **InspectCJ: Repair Package**.
The command labels retain their existing InspectCJ spelling for compatibility.
See [editor workflows](docs/editor.md).

**Pi:** after npm publication, run:

```sh
pi install npm:@mtghorbani/inspectcoder-cj@0.2.0
```

Before publication, install the npm tarball into a local directory, then register that installed package directory with Pi:

```sh
npm install --prefix ./inspectcoder-client ./mtghorbani-inspectcoder-cj-0.2.0.tgz
pi install ./inspectcoder-client/node_modules/@mtghorbani/inspectcoder-cj
```

**OpenCode:** after npm publication, merge this into `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@mtghorbani/inspectcoder-cj@0.2.0"]
}
```

For the local tarball, use `npm install` as above and point the plugin entry to the installed package's `dist/opencode.js` file URL.
Use a proper `file:///` URL with spaces encoded as `%20`.
Restart the host after changing its plugin configuration; Pi also supports `/reload` for installed extensions.
See [agent plugin instructions](plugins/agent-tools/README.md) for tools, permissions, and examples.

If a GUI host uses a custom CLI directory, save its absolute launcher path once:

```sh
inspectcoder-cj configure --cli /absolute/path/to/inspectcoder-cj
```

Client-specific executable settings remain available for intentional overrides.
Client discovery never searches the current workspace for an executable.
Optional legacy environment variables remain supported for automation, but none are required for this installation flow.
Existing `CJC`, `CJDB`, `CJPM`, `CANGJIE_HOME`, `INSPECT_CJ_SYSROOT`, and `CJDB_PYTHON_FRAMEWORK` overrides take precedence over saved settings.
Remove stale overrides from the environment used to launch a host if it selects an older SDK despite your saved configuration.

## 5. Supply acceptance, repair, review, apply

A standalone program needs independently known behavior:

```sh
inspectcoder-cj repair ./main.cj --expect-line 10 --non-interactive -- 4
```

Existing native packages use their own immutable assertions:

```sh
inspectcoder-cj test ./my-package
inspectcoder-cj repair ./my-package --non-interactive
```

Review `candidate.diff` under the JSON result's `output` directory, then explicitly apply that run:

```sh
inspectcoder-cj apply /absolute/path/to/run
```

Repairs do not overwrite original source.
Apply refuses stale sources, changed candidates, or altered assertions.
Pi requires an interactive apply confirmation; OpenCode asks its host permission system; VS Code asks before writing.
Headless Pi can repair and review, but explicit application must be performed separately through the CLI.
`needs_input` means acceptance or target selection is missing, while `compile_only` does not prove semantic correctness.

## Troubleshooting and updates

| Symptom | Action |
| --- | --- |
| CLI unavailable | Install the wheel, check `uv tool dir --bin`, or save `configure --cli` |
| Compiler/CJDB unavailable | Save `configure --sdk` or explicit `--cjc` / `--cjdb` paths |
| Apple SDK linker errors | Save a compatible `--sysroot`; inspect the doctor build diagnostic |
| Python framework loader error | Install the SDK-compatible Python framework and save its prefix if discovery fails |
| Invalid config | Fix the named user config file; unknown, duplicate, relative, or malformed values are refused |
| Missing model authentication | Complete the selected transport's login flow; host conversation login does not replace repair transport login |
| Native layout/version unsupported | Use the documented certified SDK and supported package layout |

Use `uv tool upgrade inspectcoder-cj` after future PyPI releases, and update each host package through its own package manager.
Keep the engine and clients on matching release versions while upgrading.
For source installations, use [CONTRIBUTING](CONTRIBUTING.md).
