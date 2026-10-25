<p align="center">
  <img src="https://raw.githubusercontent.com/antlobach/Dynra/main/assets/dynra-logo.svg" alt="Dynra" width="860">
</p>

<p align="center">
  <strong>Live Python runtime for coding agents.</strong><br>
  Inspect, patch, and verify running software without restarting the process or losing its state.
</p>

<p align="center">
  <a href="https://github.com/antlobach/Dynra/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/antlobach/Dynra/ci.yml?branch=main&amp;style=flat-square&amp;label=tests" alt="Tests" height="20"></a>
  <a href="https://github.com/antlobach/Dynra/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/coverage-%E2%89%A565%25-brightgreen?style=flat-square" alt="Coverage ≥65%" height="20"></a>
  <img src="https://img.shields.io/badge/python-3.10--3.14-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.10–3.14" height="20">
  <a href="https://pypi.org/project/dynra/"><img src="https://img.shields.io/pypi/v/dynra?style=flat-square&amp;logo=pypi&amp;logoColor=white" alt="PyPI" height="20"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/antlobach/Dynra?style=flat-square" alt="Apache-2.0 license" height="20"></a>
  <a href="https://deepwiki.com/antlobach/Dynra"><img src="https://raw.githubusercontent.com/antlobach/Dynra/main/assets/deepwiki-badge.svg" alt="Ask DeepWiki" height="20"></a>
</p>

## Overview

Dynra lets a coding agent develop Python software interactively inside a persistent process. The agent can navigate project modules, execute arbitrary Python in their namespaces, add or redefine functions and classes, and exercise each change immediately against the running application.

Compatible function and class identities stay stable while the software evolves. Existing imports, registries, closures, partials, and class instances can continue to refer to the patched objects.

```mermaid
flowchart LR
    A[Coding agent] -->|dynra_repl| M[MCP adapter]
    M --> R[Long-lived IPython process]
    R --> N[Project module namespaces]
    R --> P[Live patch engine]
    P --> O[Existing references and instances]
    O --> V[Verification against preserved state]
    V --> A
```

## Capabilities

- Discovers modules from the repository root, `src/`, and configured setuptools, Poetry, or Hatch source roots.
- Lists an unloaded module's public symbols without importing it.
- Executes arbitrary Python in a selected module namespace.
- Preserves compatible function and class identities across redefinitions.
- Updates existing imported references and mutable holders.
- Migrates existing instances through an optional `__dynra_update__` hook.
- Skips registration decorators when replacing an existing decorated definition.
- Rolls back definition changes when a multi-definition evaluation fails.
- Shows, reverts, and persists live changes.

## Install and start

Add Dynra as a development dependency of the target project:

```bash
cd /path/to/target-project
uv add --dev dynra
```

Start Dynra from the target project's root:

```bash
uv run dynra
```

Dynra runs in the target project's virtual environment, so project dependencies
are importable. It reads the target's `pyproject.toml`, activates its source
roots, and indexes its Python modules without importing them. Dynra does not
start the application; import or bootstrap the application explicitly when its
live state is needed. The runtime listens on `127.0.0.1:9999` by default. Use
`uv run dynra --port PORT` to select another port.

Each runtime registers its identity, project root, process, interpreter, and
endpoint in the local Dynra registry after its socket is ready. Manage running
runtimes without scanning ports:

```bash
dynra list
dynra status RUNTIME_ID
dynra stop RUNTIME_ID
dynra clean
```

`status` and `stop` also accept an unambiguous port or project path. `clean`
validates registered processes against the Dynra handshake and removes stale
records. Set `DYNRA_RUNTIME_DIR` to override the registry location.

### Explicit project bootstrap

Dynra never starts the application automatically. A project can declare an
optional module or callable that developers invoke explicitly:

```toml
[tool.dynra]
bootstrap = "dev.dynra:bootstrap"
```

```python
def bootstrap(namespace):
    service = create_development_service()
    namespace["service"] = service
    return {"client": service.test_client()}
```

Run it inside Dynra with `bootstrap()`. The callable receives the persistent
`user` namespace and may mutate it or return a mapping of names to add.
Successful bootstraps run once unless `bootstrap(force=True)` is requested.
`bootstrap_status()` reports configuration, attempts, completion, and exports.

## Agents

The repository includes a [Dynra agent skill](.agents/skills/dynra/SKILL.md). Give that skill to an agent that can call the MCP server. It defines the live-development loop, patching constraints, failure handling, and the point at which a verified live change should become a source edit.

In a project-local MCP configuration, run the installed adapter through the
project environment:

```json
{
  "mcpServers": {
    "dynra": {
      "command": "uv",
      "args": ["run", "dynra-mcp"]
    }
  }
}
```

The MCP client must launch this command with the target project as its working
directory. If the configuration is global, set its supported `cwd` option to
the target project. Start `uv run dynra` separately before using the tool.

The adapter exposes one tool:

```text
dynra_repl(code, module="user", create=False, port=9999)
```

`port` selects an existing long-running Dynra/IPython process on `127.0.0.1`; it does not start a new process. The value must match the runtime's `dynra --port PORT` option. `module` selects the namespace before evaluation. Imports are strict, so missing modules and import-time failures return errors. Set `create=True` only for an intentional synthetic scratch module. Responses always contain `success`. Dynra includes `result`, `stdout`, `stderr`, and `error` only when they carry information, and includes `source_sync` only for live changes, healing, conflicts, or source errors. An absent `source_sync` means the selected module is clean.

### Agent workflow

1. Discover the project's modules without importing them.
2. Enter a module and inspect its live namespace.
3. Execute focused expressions to explore behavior or build the next increment.
4. Add or redefine a complete function or class in its owning module.
5. Exercise the change through a real consumer while process state remains alive.
6. Inspect the live-versus-disk diff.
7. Persist the definition or apply the equivalent source edit, then run the project's durable checks.

Example:

```text
dynra_repl(code="ls_md()")
dynra_repl(code="dir_md('shop.pricing')")

dynra_repl(
  module="shop.pricing",
  code="source(calculate_total)",
)

dynra_repl(
  module="shop.pricing",
  code="def calculate_total(cart):\n    return sum(item.price * item.quantity for item in cart)",
)

dynra_repl(
  module="shop.checkout",
  code="active_checkout.total()",
  port=9999,
)

dynra_repl(
  module="shop.pricing",
  code="source_patch()",
)
```

A successful live patch proves the behavior in the current process. It does not replace source control or the project's test suite.

## Namespace model

Dynra starts in a synthetic `user` namespace. Use the `module` argument on `dynra_repl` for atomic namespace selection. Within the REPL, these commands manage namespaces:

- `require_md(name)` imports a real module and preserves the original import failure.
- `create_md(name)` creates a synthetic namespace without entering it.
- `in_md(name)` enters an existing real or synthetic namespace.
- `ls_md()` separates discovered, loaded-project, synthetic, and external modules.
- `dir_md(name)` statically inspects an unloaded project module or lists a loaded module.

Dynra supplies its commands through REPL builtins. It does not add those names to application module globals.

## Source healing

Before each evaluation, Dynra checks the source-file signatures of tracked project modules. The clean path performs metadata checks only. Dynra parses a file after its signature changes.

External edits to top-level functions and classes are applied automatically to the existing live objects. Prior imports and compatible instances therefore receive the new behavior without module reloads. Added definitions are evaluated in the live namespace. Deleted definitions are removed from the owning module and direct module imports. Each healed batch increments the module generation and appears in the response's `source_sync` field.

A module can repair derived state through an opt-in hook:

```python
def __dynra_reconcile__(changes):
    if any(change[\"definition\"].endswith(\".build_routes\") for change in changes):
        rebuild_routes_in_place()
```

Dynra does not rerun arbitrary assignments, imports, or top-level side effects. Constants and copied values retain normal Python semantics. If both disk and the REPL changed the same definition, Dynra keeps the live definition and reports a conflict instead of guessing. `persist_source()` uses the captured base definition and refuses to overwrite an external edit.

## Live patch model

Remote code executes one top-level statement at a time. After each function or class definition, Dynra reconciles the new object with the live object:

- Safe function mode refuses incompatible closure layouts and sync/async changes when old objects still have direct holders.
- Safe class mode refuses incompatible metaclass, base-class, and `__slots__` changes.
- Existing decorated definitions retain their registration because Dynra does not rerun their decorators.
- A multi-definition request restores changed definitions if a definition is refused or evaluation fails.

Transactions cover definition changes. They cannot undo arbitrary external side effects from ordinary statements or a first-time decorator. Native extension state, process configuration, and incompatible object layouts can still require a restart.

### Evolving existing instances

Add `__dynra_update__` when a compatible class patch needs to initialize state on existing instances:

```python
class Session:
    def __init__(self, user):
        self.user = user

    def label(self):
        return f"{self.user}:{self.status}"

    def __dynra_update__(self):
        if not hasattr(self, "status"):
            self.status = "active"
```

After the patch, existing `Session` instances use the new methods and run `__dynra_update__`.

## Runtime commands

| Command | Purpose |
| --- | --- |
| `create_md(name)` | Create a synthetic scratch module. |
| `require_md(name)` | Import a real module and surface its complete import error. |
| `in_md(name)` | Enter an existing real or synthetic module. |
| `ls_md()` | List discovered, loaded-project, synthetic, and external modules. |
| `dir_md(module=None)` | List symbols, including static inspection of unloaded project modules. |
| `doc(obj)` / `source(obj)` | Show documentation or live source. |
| `find_var(pattern)` | Search loaded modules for matching symbol names. |
| `function_patch_mode(mode=None)` | Read or set `safe` or `permissive` function patching. |
| `class_patch_mode(mode=None)` | Read or set `safe` or `permissive` class patching. |
| `unmap(name)` | Delete a binding and report remaining direct holders and qualified module aliases. |
| `patch_log()` | Show applied, refused, reverted, and persisted operations. |
| `revert(target)` / `revert_all()` | Restore one or all live definition snapshots; `target` may be the live function/class object or a qualified name. |
| `diverged()` / `source_patch()` | Show unified diffs between live definitions and source files. |
| `persist_source(target=None)` | Write exact evaluated definitions to their project source files. |
| `source_sync_status(module=None)` | Show source freshness, conflicts, live definitions, and generation. |
| `help_dynra()` | Show runtime help. |

## Terminal client

The interactive runtime accepts normal IPython input. `dynra-client` sends a one-off evaluation to an existing runtime:

```bash
dynra-client --module package.module "expression"
dynra-client --module scratch --create "value = 1"
```

## Security

Dynra executes arbitrary Python by design. Use it only for trusted local development. Do not expose its TCP port to untrusted networks, and do not grant untrusted agents access to the MCP server.

## Development

Develop Dynra in its own environment:

```bash
git clone https://github.com/antlobach/Dynra.git
cd Dynra
uv sync --frozen --group dev
uv run pytest -q
uv build
```

To exercise an unreleased checkout from another project, install it there as an
editable development dependency:

```bash
cd /path/to/target-project
uv add --dev --editable /path/to/Dynra
uv run dynra
```

Before committing that project's dependency files, replace the local path with
the published package:

```bash
uv remove --dev dynra
uv add --dev dynra
```

CI runs the test suite on Python 3.10 through 3.14 and builds the package.

## License

Dynra is licensed under the [Apache License 2.0](LICENSE).
