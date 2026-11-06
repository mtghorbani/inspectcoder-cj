# InspectCoder CJ

Evidence-grounded repair for Cangjie programs using the real compiler, CJDB debugger, and native `cjpm` assertions.
Use it from the command line, VS Code, OpenCode, or Pi.

InspectCoder CJ repairs a private snapshot, validates the candidate, and returns a diff.
Your original implementation changes only after an explicit guarded apply.
Expected behavior comes from your assertions or independent expected output, never from the buggy program's output.

**[Getting started](https://github.com/mtghorbani/inspectcoder-cj/blob/main/GETTING_STARTED.md)** · **[Publishing guide](https://github.com/mtghorbani/inspectcoder-cj/blob/main/docs/publishing.md)** · **[MIT license](LICENSE)**

## Install

Version 0.2.0 is prepared for publication; this repository does not automatically upload releases.
Until the maintainer publishes it, install the locally built wheel, VSIX, and npm package using the getting-started guide.
After publication, the normal install commands are:

```sh
uv tool install --python python3.11 inspectcoder-cj==0.2.0
inspectcoder-cj configure --sdk "$HOME/Tools/cangjie"
inspectcoder-cj doctor
```

The CLI also keeps the `inspect-cj` command for existing integrations.
No author-specific SDK path, shell export, or repository-local virtual environment is required by an installed package.
You install a compatible Cangjie SDK and model transport separately.
The shared engine is certified for native cjpm workflows on macOS arm64 with Cangjie SDK 1.1.0 and a compatible Apple SDK.
Windows, remote/browser extension hosts, other native SDK versions, and other native package platforms are not certified.
See the setup guide before assuming a newer compiler or Apple SDK is compatible.

## Choose an interface

| Interface | Distribution | Setup |
| --- | --- | --- |
| CLI | Python wheel / PyPI `inspectcoder-cj` | [Getting started](GETTING_STARTED.md) |
| VS Code | VSIX / Marketplace `mtghorbani.inspectcoder-cj` | [VS Code](docs/editor.md) |
| OpenCode | npm `@mtghorbani/inspectcoder-cj` | [Agent plugins](plugins/agent-tools/README.md) |
| Pi | Same npm package with an explicit Pi extension manifest | [Agent plugins](plugins/agent-tools/README.md) |

OpenCode and Pi expose six tools: doctor, test, debug, repair, review, and apply.
All clients use the same Python engine and saved SDK configuration.
Their executable discovery supports normal user CLI installations and GUI sessions with a limited PATH.

## Repair a program

```sh
inspectcoder-cj repair ./main.cj \
  --task 'Print the inclusive sum from 1 through n' \
  --expect-line 10 --non-interactive -- 4
```

For existing native assertions:

```sh
inspectcoder-cj test ./my-package
inspectcoder-cj repair ./my-package --non-interactive
```

Read the returned JSON `output` path and inspect its `candidate.diff`.
Apply only the reviewed candidate:

```sh
inspectcoder-cj apply /path/to/repair/run
```

The CLI refuses changed source, altered acceptance inputs, or modified candidates.
`success` represents passing acceptance, while `compile_only` proves compilation only and `needs_input` asks for missing independent acceptance.
The default repair transport is an authenticated Codex CLI; it is separate from the model selected in OpenCode or Pi.
Other existing transports and bounded automation are documented in [usage](docs/usage.md) and [automation](docs/automation.md).

## Complete CLI reference

The following is the complete `--help` output for version 0.2.0, including every subcommand.
The `inspectcoder-cj` and `inspect-cj` commands share this interface; help retains the `inspect-cj` program name for compatibility.

<details open>
<summary><code>inspectcoder-cj --help</code></summary>

```text
usage: inspect-cj [-h] [--version]
                  {configure,doctor,test,debug,repair,driver,apply} ...

Native CJDB-assisted Cangjie repair

positional arguments:
  {configure,doctor,test,debug,repair,driver,apply}
    configure           show or persist user toolchain paths
    doctor              prove native breakpoints, inspection and capabilities
    test                compile and run every configured executable test
    debug               persistent CJDB action/observation JSONL session
    repair              inspect, propose an evidence plan, patch and validate
    driver              draft a scratch driver from a supplied expression
    apply               explicitly apply a validated candidate with hash
                        guards

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
```

</details>

<details>
<summary><code>inspectcoder-cj configure --help</code></summary>

```text
usage: inspect-cj configure [-h] [--sdk SDK] [--cjc CJC] [--cjdb CJDB]
                            [--cjpm CJPM] [--sysroot SYSROOT]
                            [--python-framework PYTHON_FRAMEWORK] [--cli CLI]

options:
  -h, --help            show this help message and exit
  --sdk SDK             absolute sdk path
  --cjc CJC             absolute cjc path
  --cjdb CJDB           absolute cjdb path
  --cjpm CJPM           absolute cjpm path
  --sysroot SYSROOT     absolute sysroot path
  --python-framework PYTHON_FRAMEWORK
                        absolute python_framework path
  --cli CLI             absolute cli path
```

</details>

<details>
<summary><code>inspectcoder-cj doctor --help</code></summary>

```text
usage: inspect-cj doctor [-h] [--sysroot SYSROOT]

options:
  -h, --help         show this help message and exit
  --sysroot SYSROOT  explicit compiler SDK root; no system setting is changed
```

</details>

<details>
<summary><code>inspectcoder-cj test --help</code></summary>

```text
usage: inspect-cj test [-h] [project]

positional arguments:
  project     source, directory or project.json

options:
  -h, --help  show this help message and exit
```

</details>

<details>
<summary><code>inspectcoder-cj debug --help</code></summary>

```text
usage: inspect-cj debug [-h] [--case CASE] [--actions ACTIONS]
                        [--timeout TIMEOUT]
                        [project]

positional arguments:
  project            source, directory or project.json

options:
  -h, --help         show this help message and exit
  --case CASE        test name; defaults to the first case
  --actions ACTIONS  JSONL file; defaults to stdin
  --timeout TIMEOUT
```

</details>

<details>
<summary><code>inspectcoder-cj repair --help</code></summary>

```text
usage: inspect-cj repair [-h] [--compile-only] [--output OUTPUT]
                         [--replay REPLAY] [--case CASE] [--editable EDITABLE]
                         [--task TASK] [--driver DRIVER]
                         [--stdin-file STDIN_FILE] [--expect-line EXPECT_LINE]
                         [--expect-stdout EXPECT_STDOUT]
                         [--expect-stderr EXPECT_STDERR]
                         [--expect-exit EXPECT_EXIT] [--non-interactive]
                         [--transport {codex,responses,scripted}]
                         [--model MODEL] [--script SCRIPT]
                         [--max-attempts MAX_ATTEMPTS] [--max-steps MAX_STEPS]
                         [--run-timeout RUN_TIMEOUT]
                         [--model-timeout MODEL_TIMEOUT]
                         [--debug-timeout DEBUG_TIMEOUT]
                         [project]

positional arguments:
  project               source, directory or project.json

options:
  -h, --help            show this help message and exit
  --compile-only        compile without claiming semantic validation
  --output OUTPUT       new artifact directory; defaults to private user cache
  --replay REPLAY       revalidate and replay a run directory
  --case CASE           opaque native case identity from test results
  --editable EDITABLE   one implementation source relative to package
  --task TASK           intended behavior, or - to read stdin
  --driver DRIVER       immutable assertion/reproducer driver
  --stdin-file STDIN_FILE
                        program input, or - for stdin
  --expect-line EXPECT_LINE
                        exact line plus newline
  --expect-stdout EXPECT_STDOUT
                        exact stdout including newlines
  --expect-stderr EXPECT_STDERR
                        exact stderr; new cases default to empty
  --expect-exit EXPECT_EXIT
                        explicit expected process exit
  --non-interactive     never ask questions
  --transport {codex,responses,scripted}
  --model MODEL         model override; required for Responses
  --script SCRIPT       offline role/response JSON array
  --max-attempts MAX_ATTEMPTS
  --max-steps MAX_STEPS
  --run-timeout RUN_TIMEOUT
  --model-timeout MODEL_TIMEOUT
  --debug-timeout DEBUG_TIMEOUT
```

</details>

<details>
<summary><code>inspectcoder-cj driver --help</code></summary>

```text
usage: inspect-cj driver [-h] --expression EXPRESSION
                         [--expect-line EXPECT_LINE] --output OUTPUT

options:
  -h, --help            show this help message and exit
  --expression EXPRESSION
                        Cangjie expression supplied by you
  --expect-line EXPECT_LINE
                        independent expected line; never inferred
  --output OUTPUT       new scratch .cj file
```

</details>

<details>
<summary><code>inspectcoder-cj apply --help</code></summary>

```text
usage: inspect-cj apply [-h] run

positional arguments:
  run

options:
  -h, --help  show this help message and exit
```

</details>

## Develop and release

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
npm --prefix plugins/agent-tools ci --ignore-scripts
npm --prefix editors/vscode ci --ignore-scripts
.venv/bin/python -m pytest -q
node --test runtime/test/*.test.cjs
npm --prefix plugins/agent-tools run check
npm --prefix plugins/agent-tools test
npm --prefix editors/vscode run check
npm --prefix editors/vscode test
.venv/bin/python scripts/release.py check
.venv/bin/python scripts/release.py build
```

`release.py` builds local packages and checksums in `dist/release/0.2.0` and never publishes.
Native tests are opt-in and must run serially with other compiler/debugger tests.
See [contributing](CONTRIBUTING.md), [publication commands](docs/publishing.md), and [security](SECURITY.md).
Historical generated delivery manifests, `data/`, `future-plan/`, and older review records describe earlier local checkpoints and are not setup instructions or current release artifacts.
They are excluded from the public package builds.

## Attribution and limits

The method adapts InspectCoder's Inspector/Coder collaboration to Cangjie with an independently implemented native debugger bridge.
See [NOTICE](NOTICE.md), [paper mapping](docs/paper-mapping.md), and [native limitations](docs/native.md).
Known CJDB expression-evaluator limitations remain explicit failures rather than fabricated observations.
No repair tool proves correctness beyond its supplied acceptance contract.
