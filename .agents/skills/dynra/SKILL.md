---
name: dynra
description: Develop Python through a long-lived Dynra REPL. Define and evaluate small functions or classes in project modules, test them against retained application state, save verified definitions, and continue without routine restarts.
---

# Dynra

## Develop in the live process

REPL-driven development means you keep one Python process alive while you build
the program. A REPL accepts code, runs it, and returns a result. With Dynra, an
agent uses MCP tools to perform that interaction.

Start a Dynra runtime for the project. Connect the MCP adapter to that runtime.
Prepare application state when the task needs it. Then develop one small piece
at a time inside that same process.

The live session contains your working functions, classes, imported modules,
and application objects. You can inspect an object, change a function, and call
that function against the same object. You do not need to reconstruct the data
after each supported change.

Use this development loop:

1. **Choose a module.** Find the real project module that should own the code.
2. **Define a small piece.** Evaluate one complete function or class in that module.
3. **Exercise it now.** Call the function with known inputs. Inspect the result and any state changes.
4. **Improve it in place.** Evaluate a revised definition if the result is wrong.
   Test through existing callers and objects as well as direct calls.
5. **Save the verified code.** Review the live diff. Persist the tested definition to its source file.
6. **Continue in the same process.** Choose the next function or another module.
   Keep the runtime and useful application state alive.

Do not use a new Python process for each expression. Do not rewrite a whole
module and rerun the application for each small change. Do not reload a module
to make an ordinary supported function update visible.

The runtime owns the state. Individual MCP calls can use separate connections
to that same runtime. Reconnecting to it does not create a new application.
Starting another runtime does create a separate process with separate state.
The runtime does not store the agent's conversation or replace source control.

Keep full restarts to a minimum, not to zero. Restart when Dynra reports a
restart boundary or when the requested change cannot preserve the live state
safely. Never disable a safety check just to avoid restarting.

### Example: define, evaluate, save, continue

This example uses the `dynra-demo` project. Its `dynra_demo.pricing` and
`dynra_demo.app` modules already have source files. Replace `RUNTIME_ID` with
the runtime ID from discovery.

```python
dynra_evaluate(
    runtime="RUNTIME_ID",
    module="dynra_demo.pricing",
    code="def discount_total(prices, rate):\n    return round(sum(prices) * (1 - rate), 2)",
)
dynra_evaluate(
    runtime="RUNTIME_ID",
    module="dynra_demo.pricing",
    code="discount_total([10, 20], 0.1)",
)
dynra_evaluate(
    runtime="RUNTIME_ID",
    module="dynra_demo.app",
    code="from dynra_demo.pricing import discount_total\ndiscount_total(basket.prices, 0.1)",
)
dynra_diff(runtime="RUNTIME_ID")
dynra_persist(
    runtime="RUNTIME_ID",
    target="dynra_demo.pricing.discount_total",
)
```

The direct call should return `27.0`. The application call uses the existing
`basket` and its current prices. If the behavior is wrong, redefine
`discount_total` in `dynra_demo.pricing` and repeat the calls before saving it.

Persistence saves the function definition. It does not save the test expression,
the temporary import in `dynra_demo.app`, or the basket's data. Add any imports
that the saved application needs through normal source edits.

## Start once and reconnect

### Project and MCP setup

Dynra is a development dependency in the target project:

```bash
uv add --dev dynra
uv run dynra --port 0
```

Discover existing runtimes before starting another one. If none belongs to the
target project, start this command from that project's root. Keep it running
in a managed terminal or background process. Port `0` lets the OS assign a
free port. Dynra registers the actual endpoint after its server is ready.

Dynra indexes project modules without importing all of them. It does not start
the application. Check `[tool.dynra].bootstrap` when you need application state.
Call the configured bootstrap once, or use the project's explicit development
startup procedure. Do not repeat startup on each tool call.

The MCP process must run from the target project through its environment:

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

## Tool selection

Use `dynra_evaluate` for live Python code. Use the other tools for discovery,
source inspection, diffs, persistence, and lifecycle operations.
`dynra_repl` is the older port-based evaluation tool. Prefer runtime IDs so
each call selects the same project process.

| Need | Tool | Constraint |
| --- | --- | --- |
| Discover live projects | `dynra_runtimes()` | Select by runtime ID or unambiguous project path; never scan or guess ports. |
| Verify target | `dynra_status(runtime)` | Confirm project root, process identity, generation, and source state. |
| Browse modules | `dynra_modules(runtime)` | Does not require importing every module. |
| Browse symbols | `dynra_symbols(module, runtime)` | Prefer before import/evaluation. |
| Read implementation | `dynra_source(target, runtime)` | Target is a qualified top-level function or class. |
| Start configured app state | `dynra_bootstrap_status`, `dynra_bootstrap` | Explicit only; successful bootstrap is once unless forced. |
| Explore or patch | `dynra_evaluate(code, module, create, runtime)` | Select the owning module. Use `create=True` only for an intentional scratch module. |
| Inspect divergence | `dynra_diff(runtime)` | Review before persistence. |
| Read audit history | `dynra_events(after, limit, runtime)` | Save the last sequence and request later events by cursor. |
| Read patch history | `dynra_patch_log(runtime)` | Use when definition state is unclear. |
| Undo | `dynra_revert(target, runtime)` | Revert failed hypotheses before trying another. |
| Save | `dynra_persist(target, runtime)` | Persist only a behaviorally verified definition. |
| Plan resource work | `dynra_lifecycle_plan(definitions, source_before, source_after, runtime)` | Pass the same source maps intended for reconcile. No mutation; inspect safety, restart flag, affected/preserved components, unmanaged holders, and refusals. |
| Reconcile resources | `dynra_lifecycle_reconcile(...)` | Call only for a safe plan backed by adapters. |
| Recover lifecycle state | `dynra_lifecycle_journal(runtime)` | Inspect swap, cleanup, compensation, and recovery state before further mutation. |

Every response has `success`. Read `result`, `stdout`, `stderr`, and `error` when
present. `source_sync` appears when source state is actionable. A missing
`source_sync` means no actionable source state for that response; do not issue a
status call after every clean evaluation.

## Select the runtime and module

Call `dynra_runtimes` to find the target project. Use its runtime ID for later
calls. Check the project path and Python executable with `dynra_status` before
the first change. Do not guess or scan ports.

Use `dynra_modules` to list project modules. Use `dynra_symbols` and
`dynra_source` to inspect code before importing it. Public symbol listings omit
names that start with `_`. Private definitions still support evaluation and
compatible live updates.

Pass the Python import name, such as `shop.pricing`, to `dynra_evaluate`.
Do not pass a file path. A source folder such as `src/` is not part of the
import name unless the project defines it as a package.

Each evaluation selects its module before it runs the code. Code in
`shop.pricing` can use the imports and variables that exist in `shop.pricing`.
Code in `shop.checkout` sees that module's own names. A module does not gain
another module's names just because you evaluated there earlier.

Switch modules by changing the `module` argument on the next tool call.
Keep the same runtime ID. The objects in previously visited modules remain
alive. Do not depend on whichever module another client last selected.

Define a function in its owning module. If checkout imported a function from
pricing, redefine it in pricing. Then call it through checkout's existing
reference to check that the change reached its real consumer.

### Terminal commands and scratch work

In the terminal REPL, `in_md("shop.pricing")` switches the active module.
`require_md("shop.pricing")` imports it without switching.
`ls_md()` lists modules. `dir_md("shop.pricing")` lists its public names.

The initial `user` module is for experiments without a project source file.
`create_md(name)` or `create=True` creates another scratch module.
Use scratch modules for disposable exploration, not for application code that
you intend to save through `dynra_persist`.

For a new project module, create its minimal `.py` file and package structure
in the correct source folder. Then evaluate in that real module.
Dynra can resolve a new importable module without restarting the process.
Never create a scratch module to hide a failed project import.

### First imports and application state

The first evaluation in an unloaded module imports it. Python executes that
module's top-level code, including assignments and imports. Inspect it first if
importing it could start services or perform external actions.

Call `dynra_bootstrap_status` before starting configured application resources.
Call `dynra_bootstrap` once when the task needs those resources.
Do not bootstrap for static inspection or a pure function experiment.
Keep the application's objects in their real modules rather than recreating
them in each evaluation.

## Test and save each increment

Inspect `success`, `result`, `stdout`, `stderr`, and `error` after evaluation.
Check results against known inputs and expected behavior. For stateful changes,
call an existing instance, route, callback, or imported reference.

Send one complete top-level definition at a time. Keep safe update modes
enabled. Other Python code can observe separate definition publications between
calls. An evaluation can change data or cause external effects that definition
rollback cannot reverse.

Use `dynra_diff` to compare live definitions with source. Save only verified
definitions with `dynra_persist(target="shop.pricing.function_name", ...)`.
Dynra can append a new definition to an existing project module file.
It refuses persistence if the target has no project source file.

Persistence does not save ordinary assignments, imports, configuration,
dependencies, or runtime data. Update the required source files for these
changes. Recheck the saved definition through the live consumer.
Use version control and the project's tests to check the durable result.
You can run those checks without discarding the development runtime.

Use `dynra_revert` to undo a failed definition change before another approach.
Record an event cursor when the history needs to distinguish several attempts.
Do not force persistence over an external source edit.

### Interpret source status

Inspect `source_sync` when a response includes it:

- `healed`: Dynra applied supported edits from the source file.
- `live-newer`: The session contains definitions that you did not save yet.
  This is not an evaluation failure. Test the code before persisting it.
- `conflict`: The source file and live session changed the same definition.
  Stop and choose which version to keep.
- `restart-required`: A change crossed a boundary that live updates cannot handle.
- `error`: Repair the reported source error before further changes.

If managed application resources need an update, request `dynra_lifecycle_plan`
before changing them. Continue only if the plan is safe and needs no process
restart. Use `dynra_lifecycle_reconcile` to apply that reviewed plan.
Read `dynra_lifecycle_journal` after a failure. Do not continue if it reports
manual recovery is required.

## When a restart is necessary

Keep the runtime alive while useful state remains and supported updates work.
A restart can be necessary after dependency or interpreter changes, unsafe
imports, incompatible object layouts, native changes, or unmanaged resource
changes. Dynra cannot rewrite executing or suspended frames.

Preserve the diagnostic evidence before a required restart. After restarting,
discover the new runtime ID and rebuild application state through the project's
startup procedure. Do not reuse assumptions about objects from the old process.
Do not weaken safe modes or repeatedly retry an unsafe change.

## Patch and source rules

- Keep safe function and class patch modes enabled. A refusal protects a live
  holder, closure layout, sync/async boundary, metaclass, bases, slots, or other
  invariant.
- Compatible function patches preserve object identity, including prior imports
  and direct references. Compatible class patches update the existing class and
  instances.
- Use a normal-purpose `__dynra_update__(self)` only when compatible existing
  instances need initialization after a class change.
- Existing decorators are not rerun during redefinition or source healing.
  Verify the framework registry through public behavior.
- Definition batches roll back definition state on failure. Arbitrary ordinary
  statements can have irreversible side effects; keep evaluation focused.
- External top-level function/class edits heal on the next tracked evaluation.
  Dynra never blindly executes changed imports or arbitrary assignments.
- A module may opt literal constants into healing with
  `__dynra_reloadable__ = ("NAME",)`. Undeclared constants retain their live
  value. Changed imports, decorators, removed declared constants, and declared
  non-literals report `restart-required`.
- Use `__dynra_reconcile__(changes)` only to rebuild derived state after healed
  definitions. Keep it deterministic and idempotent.
- `persist_source` refuses to overwrite an external conflicting edit. Inspect
  the diff and choose deliberately; never force around the conflict.
- Audit events are append-only and deep-copied. A rolled-back transaction keeps
  its attempted/refused events followed by a `rolled-back` transaction event.

## Lifecycle rules

Lifecycle support is optional and development-only. Application modules must
not import Dynra, use Dynra proxy domain values, or require Dynra decorators.
Framework semantics belong in separately installed `dynra.adapters` packages.
Project-only semantics must be explicitly opted into with
`[tool.dynra].adapters = ["module:adapter"]`; Dynra does not scan application
modules. Adapter discovery is lazy and failure-atomic.

A lifecycle plan is mandatory before mutation. Check:

- exact changed definitions;
- patched and restarted items;
- affected and preserved components;
- unmanaged holders;
- required process restart;
- evidence-backed refusals.

Adapters prepare and validate replacements before definition or holder
publication. A pre-swap failure leaves the old system active. A post-swap
failure triggers holder rollback and compensation when safe. The journal
identifies `old-system-active`, `old-system-restored`, `new-system-committed`,
or `manual-recovery-required`. Do not continue automatically after manual
recovery is required.

When `source_after` contains a managed definition, lifecycle reconciliation
accepts one side-effect-free function definition. It refuses classes,
decorators, extra top-level statements, multi-definition batches, and eager
defaults or annotations that can call user code; restart for those changes.
This prevents staging from invoking metaclasses, subclass hooks, or shared-state
mutations before validation.

For an accepted function, reconciliation quiesces affected components and
compiles the candidate outside the live module. Staging must preserve the live
function's postponed-annotation compiler flag; resolved and unresolved
annotations must not change from strings to objects. Adapters must consume the
candidate from `ChangeSet.staged_definitions`; they must not read the old live
global while preparing. The function publishes only after all replacements
validate. Definition and resource rollback share one failure boundary. Missing
quiescence or compensation is a refusal; never apply the definition separately.

Only the affected component subgraph is rebuilt; dependencies outside it must
retain identity. Read related components and bindings from one
`LifecycleRuntime.snapshot()` generation. Ordinary module-global reads and
separate holder reads can straddle publication and are not a transaction.

`BindingKind.PRESERVED` retains identity, `RECOMPUTE` and `REGISTRY` require
transactional prepare/validate operations, `COMPONENT` follows its owning
component, and `RESTART` is a process boundary. Never reinterpret one kind as
another.

For Stario projects, install `dynra-stario` as a development dependency and
load it before route registration. The development bootstrap must call
`dynra_stario.compose_routes(app, build_routes)` around a side-effect-free route
composer. Dynra then replays the current composer on the App's owner loop, gates
new requests during reconciliation, and atomically publishes the replacement
router while preserving the live App, listener, Relay, active requests,
unaffected tasks, and streams. Production can call `build_routes(app)` directly.
A changed running task requires an explicit `TaskSupervisor` policy or refuses.
The adapter does not start or stop the application.

Task replacement follows an explicit policy: `never`, `safe-point`, `immediate`,
`drain`, `rolling`, or `manual`. Do not restart a task under a different policy
because it is convenient. Currently executing and suspended frames are not
rewritten.

## Failure handling

- **No runtime:** start `uv run dynra --port 0` from the target project in a
  managed process, then discover its registered runtime ID. The MCP adapter
  does not start the application or runtime.
- **Ambiguous runtime:** use the exact runtime ID returned by `dynra_runtimes`.
- **Import failure:** fix the environment, dependency, or module. Do not use
  `create=True` as fallback.
- **Patch refusal:** inspect holders and compatibility evidence. Make a
  compatible change, install/configure an adapter, or restart.
- **Unexpected behavior:** inspect incremental events, patch log, and diff; then
  revert before testing a different hypothesis.
- **Source conflict:** stop mutation, inspect live and disk definitions, then
  explicitly choose one. Persistence will not overwrite disk.
- **Restart required:** preserve evidence and restart. Do not claim convergence.
- **Lifecycle failure:** read the journal and recovery state. If manual recovery
  is required, stop automated mutation and report the exact incomplete cleanup
  or compensation.

## Completion criteria

A Dynra-assisted change is complete only when:

1. behavior was reproduced before the change where practical;
2. the patch was exercised through the real consumer;
3. source state has no unresolved conflict or restart-required divergence;
4. affected lifecycle work committed safely or was not needed;
5. the verified definition is represented in source;
6. durable tests/checks and a relevant smoke path pass.
