<p align="center">
  <img src="https://raw.githubusercontent.com/antlobach/Dynra/main/assets/dynra-logo.svg" alt="Dynra" width="860">
</p>

<p align="center">
    <strong>Develop Python in a long-lived session.</strong><br>
    Define code, evaluate it, and test changes against the same live application state.
</p>

<p align="center">
  <a href="https://github.com/antlobach/Dynra/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/antlobach/Dynra/ci.yml?branch=main&amp;style=flat-square&amp;label=tests" alt="Tests" height="20"></a>
  <a href="https://github.com/antlobach/Dynra/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/coverage-%E2%89%A565%25-brightgreen?style=flat-square" alt="Coverage ≥65%" height="20"></a>
  <img src="https://img.shields.io/badge/python-3.10--3.14-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.10–3.14" height="20">
  <a href="https://pypi.org/project/dynra/"><img src="https://img.shields.io/badge/pypi-v0.4.1-ea7233?style=flat-square&amp;logo=pypi&amp;logoColor=white" alt="PyPI v0.4.1" height="20"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/antlobach/Dynra?style=flat-square" alt="Apache-2.0 license" height="20"></a>
  <a href="https://deepwiki.com/antlobach/Dynra"><img src="https://raw.githubusercontent.com/antlobach/Dynra/main/assets/deepwiki-badge.svg" alt="Ask DeepWiki" height="20"></a>
</p>

## REPL-driven Python development

Dynra lets you build a Python application inside a process that stays alive.
You can work from Emacs or an AI agent.

In REPL-driven development, you define a small piece of code, run it, inspect
the result, and refine it in the same session. Your application objects,
loaded data, and imports remain available between evaluations.

You can move between project modules and run code in each module's context.
For example, change a pricing function in `shop.pricing`. Then call an existing
basket in `shop.checkout` to test the change against its current items.
Supported updates keep the existing objects and imported references in use.

The development loop is:

1. Start Dynra for the project. Prepare application state when you need it.
2. Select a module and evaluate one function or class.
3. Call it with known inputs or through existing application objects.
4. Refine the definition until the result is correct.
5. Save the verified definition to its source file.
6. Continue with the next piece in the same session.

You do not need to restart the application and reconstruct its state after
each supported change. Keep the process alive while you develop.
Some changes still require an adapter or a full restart.

Dynra does not start your application for you. Start it once through your
development setup or an explicit bootstrap. Saving definitions does not save
runtime data or ordinary assignments. Source files and tests remain the
lasting record of your work.

See the [agent skill](.agents/skills/dynra/SKILL.md) for the live development
workflow, module navigation, persistence, and restart rules.

## Capabilities

- Finds modules in the project root, `src/`, and setuptools, Poetry, or Hatch source folders.
- Lists public names in a module without importing the module.
- Runs Python code in the selected module's context.
- Keeps compatible function and class objects in place when you redefine them.
- Updates existing imports and mutable objects that refer to patched code.
- Can update existing instances through an optional `__dynra_update__` method.
- Does not repeat registration decorators when it replaces an existing definition.
- Reverts definition changes when a multi-definition evaluation fails.
- Shows, reverts, and saves live code changes.

## Install and start

Add Dynra to the project's development dependencies:

```bash
cd /path/to/target-project
uv add --dev dynra
```

Start Dynra from the project root:

```bash
uv run dynra
```

Dynra uses the project's Python environment. This makes the project's installed
packages available to Dynra. Dynra reads `pyproject.toml` to find source
folders and Python modules. It finds modules without importing them.

Dynra does not start your application. Start the application yourself when you
need its live state. The default port is `9999`. Use `uv run dynra --port PORT`
to select another port.

After the server starts, Dynra records its ID, project path, process ID, Python
executable, and port in a local registry. Use these commands to manage sessions:

```bash
dynra list
dynra status RUNTIME_ID
dynra stop RUNTIME_ID
dynra clean
```

`status` and `stop` also accept a port or project path that selects one session.
`clean` checks each record and removes records for stopped or invalid processes.
Set `DYNRA_RUNTIME_DIR` to change the registry folder.

### Emacs setup

#### 1. Add Dynra to the Python project

From the project root:

```bash
uv add --dev dynra
```

#### 2. Install the Emacs plugin

Add the plugin folder to Emacs' `load-path` in your configuration.
Use `~/.emacs.d/init.el` or `~/.config/emacs/init.el`.

```elisp
(add-to-list 'load-path "/path/to/Dynra/editors/emacs")
(require 'dynra)
(dynra-auto-mode 1)
```

Change the path to your Dynra checkout. Restart Emacs or reload the configuration.
Dynra requires Emacs 27.1 or later.

#### 3. Start Dynra and evaluate Python

Open a Python file in the project. Run `M-x dynra-jack-in` or press
`C-c C-x j`. Dynra starts in the project's `uv` environment and connects
Emacs to that project's runtime.

Press `C-x C-e` to run the Python statement at point. Press `C-c C-c` to run
the function or class at point. Press `C-c C-k` to run the whole buffer.
Dynra shows results inline and in the echo area.

`dynra-auto-mode` connects to an existing runtime when you open a project file.
Auto-start is optional.

```elisp
(setq dynra-auto-jack-in t)
```

If Emacs cannot find `uv`, Dynra checks `~/.local/bin` and common Homebrew and
Linuxbrew folders. For another path, set `dynra-command` to the full path to
`uv`. See [`editors/emacs/README.md`](editors/emacs/README.md) for all commands
and settings.

### Explicit project bootstrap

Dynra does not start your application. To prepare application state, configure
a bootstrap module or function in `pyproject.toml`:

```toml
[tool.dynra]
bootstrap = "dev.dynra:bootstrap"
```

```python
def bootstrap(module_globals):
    service = create_development_service()
    module_globals["service"] = service
    return {"client": service.test_client()}
```

Run `bootstrap()` inside Dynra. A configured function receives a dictionary of
names from the `user` module. It can change that dictionary or return names
for Dynra to add. Dynra runs a successful bootstrap once.

Use `bootstrap(force=True)` to run it again. Call `bootstrap_status()` to check
the configuration, attempts, completion, and exported names.

## Agents

An AI agent can use Dynra through MCP (Model Context Protocol). The repository
includes a [Dynra agent skill](.agents/skills/dynra/SKILL.md). Give this skill
to an agent that can call an MCP server. It explains how to inspect modules,
change code, handle errors, and save tested changes.

The adapter must run in the project's Python environment.
Use this project-local MCP configuration to start the adapter:

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

The MCP client must run this command from the target project folder. For a
global configuration, set the client's `cwd` option to that folder.
Start Dynra with `uv run dynra` before the agent uses the adapter.

The adapter provides tools to find sessions, inspect modules, and run code:

```text
dynra_runtimes()
dynra_status(runtime="", port=9999)
dynra_modules(runtime="", port=9999)
dynra_symbols(module, runtime="", port=9999)
dynra_source(target, runtime="", port=9999)
dynra_evaluate(code, module="user", create=False, runtime="", port=9999)
dynra_diff(runtime="", port=9999)
dynra_persist(target="", runtime="", port=9999)
dynra_revert(target, runtime="", port=9999)
dynra_patch_log(runtime="", port=9999)
dynra_events(after=0, limit=None, runtime="", port=9999)
dynra_bootstrap(force=False, runtime="", port=9999)
dynra_bootstrap_status(runtime="", port=9999)
dynra_lifecycle_plan(definitions, source_before={}, source_after={}, runtime="", port=9999)
dynra_lifecycle_reconcile(definitions, source_before={}, source_after={}, runtime="", port=9999)
dynra_lifecycle_journal(runtime="", port=9999)
```

Use `runtime` to select a running Dynra session. Enter its ID, project path, or registered port.
Older clients can use `dynra_repl(code, module="user", create=False, port=9999)`. It connects to a session on that port. It does not start Dynra.
Imports use normal Python behavior. Set `create=True` only to make a new scratch module.

### Agent workflow

Use these tools to inspect a project and run code. Dynra returns results that agents can read.
Use `dynra_evaluate` to run expressions or replace full function and class definitions. Use `dynra_repl` only if the client cannot find sessions.

| Need | Tool | Use |
| --- | --- | --- |
| Find a running project | `dynra_runtimes` | Select a session by ID or project path. Do not guess a port. |
| Check a session | `dynra_status` | Check its project path and code state before a change. |
| Find modules | `dynra_modules`, `dynra_symbols` | Find modules and names before import. Imports can run application code. |
| Read code | `dynra_source` | Enter the full function or class name. |
| Start application state | `dynra_bootstrap_status`, then `dynra_bootstrap` | Run bootstrap only when you need application state. Dynra does not run it for you. |
| Run or change code | `dynra_evaluate` | Select a module. Send an expression or a full function or class. |
| Review changes | `dynra_diff`, `dynra_events`, `dynra_patch_log` | Read current changes and past actions. |
| Undo or save | `dynra_revert`, `dynra_persist` | Undo failed changes. Save changes only after you test them in the running application. |
| Change application parts | `dynra_lifecycle_plan` before `dynra_lifecycle_reconcile` | Apply only a safe plan. If Dynra refuses, restart or add an adapter. |
| Check an interrupted change | `dynra_lifecycle_journal` | Read the journal before you try another change. |

Follow these steps:

1. Call `dynra_runtimes` and select the project. Use its runtime ID for later calls.
2. Call `dynra_status`, `dynra_modules`, and `dynra_symbols`. Check the session and module before you change code.
3. If you need application state, check `dynra_bootstrap_status`. Then call `dynra_bootstrap` once.
4. Read the function or class with `dynra_source`. Reproduce the problem with `dynra_evaluate`.
5. Replace one full function or class in its module. Send one definition at a time.
   Keep safe function and class update modes enabled.
   Other Python code can see each definition as Dynra publishes it.
6. Test the change through existing objects, imports, routes, or callbacks. Do not test only the new function.
7. Check `source_sync` after each response. Stop if it reports `conflict`, `error`, or `restart-required`.
   Call `dynra_events(after=CURSOR)` to read later events.
8. If the change affects components or tasks that an adapter manages, call `dynra_lifecycle_plan`.
   Reconcile only when `safe` is true and `requires_process_restart` is false.
   Do not ignore a refusal for an unmanaged object or missing lifecycle operation.
9. Review `dynra_diff`. Undo a failed change or save the tested definition.
   Then update any remaining source files or configuration.
10. Run the project's tests and checks. Live testing checks the current session.
    Version control and tests check the files you will deliver.

Example:

```text
dynra_runtimes()
dynra_status(runtime="RUNTIME_ID")
dynra_symbols(module="shop.pricing", runtime="RUNTIME_ID")
dynra_source(target="shop.pricing.calculate_total", runtime="RUNTIME_ID")
dynra_evaluate(
  module="shop.pricing",
  code="def calculate_total(cart):\n    return sum(item.price * item.quantity for item in cart)",
  runtime="RUNTIME_ID",
)
dynra_evaluate(
  module="shop.checkout",
  code="active_checkout.total()",
  runtime="RUNTIME_ID",
)
dynra_diff(runtime="RUNTIME_ID")
dynra_persist(target="shop.pricing.calculate_total", runtime="RUNTIME_ID")
```

Live testing shows that a change works in the current session. It does not replace version control or project tests.

## Switch between modules

Dynra starts in the scratch module named `user`. To run code in a project
module, pass its name to `dynra_repl` or call `in_md(name)`.

- `require_md(name)` imports a project module and shows its import error.
- `create_md(name)` creates a scratch module without switching to it.
- `in_md(name)` switches to an existing project or scratch module.
- `ls_md()` lists discovered, loaded, scratch, and external modules.
- `dir_md(name)` lists names in a loaded module. It can inspect an unloaded
  project module without importing it.

Dynra makes these commands available in its interactive session. They do not
become names in your application modules.

## Apply file changes to the running program

Before each evaluation, Dynra checks tracked project files. For an unchanged
file, it checks only the file metadata. Dynra reads and parses a file after it
changes.

When a tracked file changes, Dynra can update supported functions and classes
in the running process. Existing imports and compatible instances can use the
new code without a module reload. Dynra adds new definitions to the live
module. It removes deleted definitions from that module and its direct imports.
Each update batch increases the module's generation number and appears in the
`source_sync` result.

A module can define this optional function to update related application state:

```python
def __dynra_reconcile__(changes):
    if any(change["definition"].endswith(".build_routes") for change in changes):
        rebuild_routes_in_place()
```

Dynra does not run changed assignments, imports, decorators, or other code that
Python runs when it imports a module. To let Dynra update fixed values written
directly in a file, list them in `__dynra_reloadable__`:

```python
__dynra_reloadable__ = ("TAX_RATE", "FEATURE_ENABLED")
```

Dynra copies changed listed values into the live module. It leaves other
assignments unchanged. Import or decorator changes, removed listed values, and
values that are not fixed values written directly in the file need a process
restart. Dynra reports these cases as `restart-required`.

If a source file and the live session change the same definition, Dynra keeps
the live version and reports a conflict. `persist_source()` saves the live
definition only if the source file has not changed outside Dynra.

## How Dynra updates running code

Dynra runs statements in order. It checks each new function or class and tries
to update the object that is already in use.
This includes private functions, async functions, and classes with names such
as `_add` and `_Basket`. Existing imports and compatible instances receive
supported changes without a restart.

- Safe function mode refuses incompatible changes to variables captured from outer functions.
  It also refuses a change between normal and async behavior when old code still has references.
- Safe class mode refuses incompatible changes to the metaclass, base classes, or `__slots__`.
- Dynra does not run a decorator again when it replaces a decorated definition. This keeps its registration.
- If an evaluation defines several functions or classes and one fails, Dynra can undo earlier definition changes. Other Python code can read between updates.

Dynra can undo definition changes. It cannot undo effects from ordinary
statements, first-time decorators, native extensions, or process settings.
Some incompatible object changes need a restart.

### Updating existing instances

Add `__dynra_update__` to a class when existing objects need new state after a
compatible class change:

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

After the change, existing `Session` objects use the new methods. Dynra calls
`__dynra_update__` on each existing object.

## Dynra commands

| Command | Purpose |
| --- | --- |
| `create_md(name)` | Create a scratch module. |
| `require_md(name)` | Import a project module and show its import error. |
| `in_md(name)` | Switch to an existing project or scratch module. |
| `ls_md()` | List discovered, loaded, scratch, and external modules. |
| `dir_md(module=None)` | List names in a module. It can inspect an unloaded module without importing it. |
| `doc(obj)` / `source(obj)` | Show an object's help text or source code. |
| `find_var(pattern)` | Search loaded modules for names that match a pattern. |
| `function_patch_mode(mode=None)` | Read or set safe or permissive updates for functions. |
| `class_patch_mode(mode=None)` | Read or set safe or permissive updates for classes. |
| `unmap(name)` | Remove a name and report code that still refers to it. |
| `patch_log()` | List applied, refused, undone, and saved changes. |
| `events(after=0, limit=None)` | Read recorded events after a sequence number. |
| `revert(target)` / `revert_all()` | Restore one or all saved definitions by object or name. |
| `diverged()` / `source_patch()` | Show differences between live code and source files. |
| `persist_source(target=None)` | Save evaluated definitions to project files. |
| `source_sync_status(module=None)` | Show file changes, conflicts, results, restart needs, and module generation. |
| `help_dynra()` | Show Dynra help. |

## Terminal client

The Dynra terminal session accepts normal Python input. `dynra-client` sends one evaluation to a running session:

```bash
dynra-client --module package.module "expression"
dynra-client --module scratch --create "value = 1"
```

## Optional adapters for application changes

Dynra belongs in development dependencies. Production modules do not import
Dynra, use its proxy values, or add its decorators.
An optional development package can provide an adapter. The adapter tells
Dynra how to update selected application parts. Dynra calls each part a component.

```toml
[project.entry-points."dynra.adapters"]
example = "dynra_example:adapter"
```

An adapter lists the components that Dynra can update. It provides an update
rule for each component. Dynra creates a repeatable plan before each change.
The same inputs produce the same plan.

Dynra prepares and checks replacements before it changes references in one step.
If a later step fails, Dynra tries to restore the old state.

Dynra refuses changes in these cases:

- An update rule is missing.
- An adapter does not control a reference.
- Components form a dependency cycle.
- A reference requires a restart.
- No safe task policy exists.

Each refusal returns structured details to the client.

Dynra exports these adapter types and helpers: `ComponentAdapter`, `ChangeSet`,
`LifecycleRuntime`, `ManagedHolder`, `RuntimeGraph`, `TaskSupervisor`,
`TaskPolicy`, `BindingKind`, and `migrate_instances`.

Dynra loads an adapter when you request a plan or change. A project can use a
local adapter without importing Dynra in production code:

```toml
[tool.dynra]
adapters = ["my_project.dev_adapter:adapter"]
```

The adapter module must be available in the project's development environment.
Set the value to `module` or `module:attribute`. Dynra loads only listed
adapters. It does not search the project for other adapters.

Install the optional Stario adapter:

```bash
uv add --dev dynra-stario
```

The Stario adapter can keep the loaded `stario.App`, listener, Relay, active
requests, unchanged tasks, and streams while Dynra rebuilds routes and
middleware.

In the development bootstrap, call
`dynra_stario.compose_routes(app, build_routes)` to register routes.
The route function must not perform side effects because Dynra calls it again.
Dynra can then rebuild routes without restarting listeners, databases, or tasks.

The production bootstrap can call `build_routes(app)` directly. Removing Dynra
from development dependencies does not change application startup.

To replace a running task, a `TaskSupervisor` must define a replacement policy.
Dynra cannot rewrite an async task while that task is waiting.

Agents can use these tools to plan and apply lifecycle changes. The tools also
show the change history:

```text
dynra_lifecycle_plan(definitions, source_before={}, source_after={}, runtime="", port=9999)
dynra_lifecycle_reconcile(definitions, source_before={}, source_after={}, runtime="", port=9999)
dynra_lifecycle_journal(runtime="", port=9999)
```

For a managed function change, provide one full function name and the old and
new source code in `source_before` and `source_after`.

Dynra refuses to publish several definitions at once. Other Python code could
read the definitions between updates. Dynra also refuses managed changes to
classes, decorators, extra top-level statements, or expressions that call user
code while Python defines a function. Use a process restart for these changes.

For an accepted function change, Dynra pauses the affected components. It
compiles the new function in a separate dictionary. It gives the function to
adapters as `ChangeSet.staged_definitions`.

Dynra preserves the live function's rules for deferred annotations during
compilation. Resolved and unresolved annotations keep their original string
form. Dynra checks all components, references, and task replacements before
it publishes the function and the new component version.

If a change fails, Dynra runs restore actions. If they succeed, Dynra restores
the old state. Other Python code can still read different versions during a
change. Use one `LifecycleRuntime.snapshot()` call to read related values.

Dynra applies one change at a time for each runtime and shared adapter resource.
If preparation, validation, publication, or commit fails, Dynra tries to
restore the old definitions and resources.

Dynra removes old resources after it makes the new version active. If cleanup
fails, Dynra keeps the new version and records `cleanup-incomplete`. Dynra
refuses a change before it starts if it cannot pause affected work or restore
the old state after a failure.

A `TaskSupervisor` controls how Dynra replaces a running async task:

- `never`: Do not replace the task.
- `manual`: Get approval before replacement.
- `safe-point`: Wait for the task to mark a safe point.
- `drain`: Call the configured drain function.
- `rolling`: Start a replacement and wait for its readiness check.
- `immediate`: Replace the task during commit.

Dynra refuses `never` tasks and `manual` tasks without approval. A `drain` task
needs a drain function. A `rolling` readiness check must accept the replacement
task. Dynra refuses a rolling change that needs code it has not published.
Dynra also refuses decorated definitions. Put decorator registration in the
adapter's route or component setup. Dynra records each task result.

`dynra_lifecycle_plan` reports a plan. It does not change the running process.
Reconciliation updates only affected components. Unchanged components keep
their existing objects.

Dynra publishes components and references through one versioned
`LifecycleRuntime.snapshot()`. Adapter code must use one snapshot to read
related values. Separate reads can return values from different versions.

The project journal records source hashes, preparation, task results, the
change point, cleanup, restore actions, and recovery state. Use a separate
optional package for framework-specific rules.

## Security

Dynra runs arbitrary Python code. Use it only for trusted local development.
Do not expose its network port to untrusted systems. Do not let untrusted agents
use its MCP server.

## Development

Use a separate environment to develop Dynra:

```bash
git clone https://github.com/antlobach/Dynra.git
cd Dynra
uv sync --frozen --group dev
uv run pytest -q
uv build
```

To test an unreleased Dynra checkout in another project, install it as an
editable development dependency:

```bash
cd /path/to/target-project
uv add --dev --editable /path/to/Dynra
uv run dynra
```

Before you commit the project's dependency files, replace the local checkout
with the published Dynra package:

```bash
uv remove --dev dynra
uv add --dev dynra
```

CI tests Dynra on Python 3.10 through 3.14. CI also builds the package.

## License

Dynra uses the [Apache License 2.0](LICENSE).
