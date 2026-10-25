---
name: dynra
description: Use Dynra for state-preserving interactive Python development through runtime discovery, structured MCP tools, live definition patches, source healing, explicit bootstrap, lifecycle plans, verification, revert, and persistence.
---

# Dynra

Use Dynra to develop Python inside an existing long-running process. Preserve
loaded data, instances, imports, caches, registrations, and application state
while inspecting, changing, and verifying behavior. Treat the runtime as a fast
feedback environment, not as a replacement for source control or durable tests.

## When to use Dynra

Use Dynra when retained process state has value:

- reproduce a state-dependent bug in the process where it occurs;
- inspect loaded modules, objects, imports, holders, and generations;
- redefine compatible functions or classes and exercise real consumers;
- evolve existing instances without rebuilding expensive state;
- test behavior through routes, callbacks, registries, schedulers, or clients;
- compare a proven live definition with disk, then persist or revert it;
- reconcile resources only when an installed adapter supplies lifecycle semantics.

Use the ordinary edit/restart loop when no useful state exists, dependencies or
interpreter configuration changed, native state changed, currently executing or
suspended frames must change, irreversible external effects occurred, or Dynra
reports a restart boundary. Never weaken safe patching or ignore a refusal.

## Preconditions

Dynra is a development dependency in the target project:

```bash
uv add --dev dynra
uv run dynra
```

The runtime does not start the application. It indexes project modules, listens
locally, and registers its runtime ID only after the socket is ready. A project
may configure `[tool.dynra].bootstrap`; call it explicitly only when the task
needs application-owned live state.

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

Prefer structured tools. Use `dynra_evaluate` only for behavior-specific Python.
`dynra_repl` is a port-based compatibility tool; do not use it when runtime
selection is available.

| Need | Tool | Constraint |
| --- | --- | --- |
| Discover live projects | `dynra_runtimes()` | Select by runtime ID or unambiguous project path; never scan or guess ports. |
| Verify target | `dynra_status(runtime)` | Confirm project root, process identity, generation, and source state. |
| Browse modules | `dynra_modules(runtime)` | Does not require importing every module. |
| Browse symbols | `dynra_symbols(module, runtime)` | Prefer before import/evaluation. |
| Read implementation | `dynra_source(target, runtime)` | Target is a qualified top-level function or class. |
| Start configured app state | `dynra_bootstrap_status`, `dynra_bootstrap` | Explicit only; successful bootstrap is once unless forced. |
| Explore or patch | `dynra_evaluate(code, module, create, runtime)` | Select the owning module; `create=True` is only for intentional scratch namespaces. |
| Inspect divergence | `dynra_diff(runtime)` | Review before persistence. |
| Read audit history | `dynra_events(after, limit, runtime)` | Save the last sequence and request later events by cursor. |
| Read patch history | `dynra_patch_log(runtime)` | Use when definition state is unclear. |
| Undo | `dynra_revert(target, runtime)` | Revert failed hypotheses before trying another. |
| Save | `dynra_persist(target, runtime)` | Persist only a behaviorally verified definition. |
| Plan resource work | `dynra_lifecycle_plan(definitions, runtime)` | No mutation. Inspect safety, restart flag, affected/preserved components, unmanaged holders, and refusals. |
| Reconcile resources | `dynra_lifecycle_reconcile(...)` | Call only for a safe plan backed by adapters. |
| Recover lifecycle state | `dynra_lifecycle_journal(runtime)` | Inspect swap, cleanup, compensation, and recovery state before further mutation. |

Every response has `success`. Read `result`, `stdout`, `stderr`, and `error` when
present. `source_sync` appears when source state is actionable. A missing
`source_sync` means no actionable source state for that response; do not issue a
status call after every clean evaluation.

## Fully interactive development loop

1. **Discover and pin the runtime.** Call `dynra_runtimes`, select the target by
   project, then use its runtime ID for every call. Call `dynra_status` before
   mutation. Never connect to a similarly numbered port by assumption.
2. **Discover before importing.** Use `dynra_modules` and `dynra_symbols` to find
   the owning module. Import failures are evidence; never hide one with a
   synthetic namespace.
3. **Bootstrap only if needed.** Use `dynra_bootstrap_status`. Call
   `dynra_bootstrap` when the task requires the application's initialized object
   graph. Static inspection and pure function work usually do not need it.
4. **Read and reproduce.** Use `dynra_source`, then evaluate a focused expression
   through the real loaded consumer. Record the current event cursor when later
   attribution matters.
5. **Patch the owner.** Send the complete top-level function or class definition
   to its owning module. Patch one logical definition at a time unless several
   definitions must form one transaction.
6. **Verify through retained references.** Exercise prior imports, instances,
   routes, callback registries, clients, or tasks. A direct call to the new
   definition alone is insufficient when the bug is in integration state.
7. **Read source state.** Inspect `source_sync` from the response:
   - `healed`: external top-level definition edits were safely applied;
   - `live-newer`: the process contains unpersisted live definitions;
   - `conflict`: both disk and live state changed; stop and resolve explicitly;
   - `restart-required`: imports, decorators, unsafe constants, or another
     boundary changed; do not pretend the process matches disk;
   - `error`: repair the source error before further mutation.
8. **Plan lifecycle impact when resources are involved.** Call
   `dynra_lifecycle_plan` before any component reconciliation. Continue only if
   `safe` is true and `requires_process_restart` is false. Preserve unaffected
   components. Never suppress unmanaged-holder, missing-operation, task-policy,
   cycle, or restart-only refusals.
9. **Reconcile and inspect.** Call `dynra_lifecycle_reconcile` only with the
   reviewed definition set and source snapshots. On failure, inspect
   `dynra_lifecycle_journal`: determine whether the old system stayed active,
   was restored, or manual recovery is required before doing anything else.
10. **Converge with source.** Use `dynra_diff`. Revert unsuccessful work or
    persist the exact verified definition. Apply other ordinary source and
    configuration edits through the coding workflow.
11. **Run durable checks.** Execute the project's tests/checks and an actual
    smoke path. Live verification proves the retained process; checks prove the
    committed deliverable.

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

When `source_after` contains a managed definition, reconciliation quiesces the
affected components and compiles candidates outside the live module.
Adapters must consume candidates from `ChangeSet.staged_definitions`; they must
not read the old live global while preparing. Definitions publish only after all
replacements validate. Decorated definitions refuse because replaying or
stripping decorators changes external registration semantics; move registration
into adapter-owned composition. Definition and resource rollback share one
transaction boundary. Missing quiescence or compensation is a refusal; never
apply the definition separately first.

Only the affected component subgraph is rebuilt; dependencies outside it must
retain identity. Read related components and bindings from one
`LifecycleRuntime.snapshot()` generation. Separate holder reads can straddle a
swap and are not a transaction.

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

- **No runtime:** ask the developer to start `uv run dynra` in the target project;
  the MCP adapter does not start it.
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
