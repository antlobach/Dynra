---
name: dynra
description: Use Dynra for interactive, REPL-driven Python development. Navigate project modules, execute code in live namespaces, redefine functions and classes, verify behavior immediately, and persist proven changes.
---

# Dynra

Use Dynra to develop Python software interactively inside a running process. Explore namespaces, execute arbitrary Python, evolve definitions, exercise real consumers, and persist the result without restarting between each change.

## When to use Dynra

Use Dynra when developing Python software through a live REPL:

- Navigate an unfamiliar project's module layout without importing every module.
- Execute arbitrary Python in the namespace that owns the code.
- Add or redefine functions and classes, then exercise them immediately.
- Develop through real callers, routes, handlers, registries, and object graphs.
- Preserve loaded data, instances, caches, and application state while iterating.
- Inspect live-versus-disk changes and persist definitions after they work.

Dynra supports general interactive development, not only debugging. State-dependent bugs, expensive fixtures, stale imported references, and existing instances are cases where preserving the process has additional value.

Use a normal edit-and-run loop when no useful process state exists or the change requires a restart. Native extension state, interpreter startup configuration, incompatible object layouts, and dependency installation usually require a fresh process. Never connect Dynra to untrusted code, agents, or networks.

## Preconditions

The target project must run Dynra from its repository root with the project's Python interpreter:

```bash
cd /path/to/target-project
/path/to/target-project/.venv/bin/dynra --port 9999
```

The MCP adapter connects to a long-running Dynra process on `127.0.0.1`. Port `9999` is the default. If the project uses another port, pass that port to every `dynra_repl` call. Selecting a port routes the call to an existing runtime; it does not start a new REPL. If `dynra_repl` returns a connection error, start the matching runtime before continuing. Dynra executes arbitrary Python; use it only with trusted local projects and agents.

## Tool contract

```text
dynra_repl(code, module="user", create=False, port=9999)
```

- `port` selects the long-running Dynra runtime on `127.0.0.1`. It must match the runtime's `dynra --port PORT` option.
- `module` selects the namespace before executing `code`. Pass the module that owns the definition you inspect or replace.
- Dynra imports real modules strictly. Import errors are evidence; do not hide them by creating a namespace with the same name.
- Set `create=True` only for an intentional scratch module.
- State persists between calls sent to the same port, including imported modules, instances, collections, caches, and prior results.
- Read `success`, `result`, `stdout`, `stderr`, and `error` on every response. `result` is a representation of the final expression, not a serialized Python object.

## How to use Dynra

1. Discover namespaces without importing them:

   ```text
   dynra_repl(code="ls_md()")
   dynra_repl(code="dir_md('package.module')")
   ```

2. Enter the owning module and inspect the live definition:

   ```text
   dynra_repl(module="package.module", code="source(target_function)")
   ```

3. Exercise the current behavior or build the next increment with focused expressions in the live process.

4. Replace the complete top-level function or class definition in its owning module:

   ```text
   dynra_repl(
       module="package.module",
       code="def target_function(value):\n    return corrected_result(value)",
   )
   ```

5. Verify through the real consumer, not only through the patched function. Switch modules with the `module` argument instead of calling `in_md()` separately.

6. Inspect live-versus-disk changes:

   ```text
   dynra_repl(module="package.module", code="source_patch()")
   ```

7. Persist a proven definition, or apply the equivalent source edit with the coding agent:

   ```text
   dynra_repl(
       module="package.module",
       code="persist_source('package.module.target_function')",
   )
   ```

8. Run the project's durable tests and checks. A successful live evaluation proves behavior only in the current process.

## Patch rules

- Patch one logical definition at a time unless several definitions must change atomically.
- Keep safe patch modes enabled. Do not switch to `permissive` merely to suppress a refusal.
- Function patches preserve compatible object identity, including prior imports and direct references.
- Class patches update compatible existing classes and instances. Add `__dynra_update__(self)` when existing instances need new state.
- Dynra does not rerun decorators when redefining an existing decorated function or class. Verify any framework registry through its normal public behavior.
- A failed multi-definition evaluation rolls back definition changes, but arbitrary side effects from ordinary statements cannot be rolled back.
- Use `revert('package.module.name')` for one definition or `revert_all()` to discard all unpersisted live patches.
- Use `patch_log()` when the process history or current state is unclear.

## Namespace commands

| Command | Use |
| --- | --- |
| `ls_md()` | List discovered project modules and loaded namespaces. |
| `dir_md(name)` | Inspect public names without importing an unloaded project module. |
| `require_md(name)` | Import a real module and preserve its import error. |
| `create_md(name)` | Create an explicit synthetic scratch namespace. |
| `source(obj)` / `doc(obj)` | Inspect a live object. |
| `find_var(pattern)` | Search loaded modules for symbol names. |
| `diverged()` / `source_patch()` | Compare live definitions with source files. |
| `persist_source(target=None)` | Write exact evaluated definitions to source. |
| `patch_log()` | Inspect applied, refused, reverted, and persisted operations. |
| `revert(target)` / `revert_all()` | Undo live definition changes. |

## Failure handling

- **Import failure:** fix the environment, dependency, or module error. Do not use `create=True` as a fallback.
- **Patch refusal:** inspect holders and compatibility constraints. Prefer a compatible definition or restart when the change requires a new closure layout, sync/async boundary, metaclass, base class, or slots layout.
- **Unexpected behavior after a patch:** inspect `patch_log()` and `source_patch()`, then revert before testing a different hypothesis.
- **Source changed outside Dynra:** inspect the live and disk definitions before persisting. Do not overwrite a concurrent edit blindly.
