# Optional Runtime Architecture

## Status

Design proposal.

## Problem

Dynra can transparently replace compatible Python functions and classes, repair imported references, and heal source changes. It cannot safely infer every lifecycle operation created by arbitrary application bootstrap code. Stateful resources such as servers, database pools, relays, schedulers, consumers, middleware chains, and running coroutine frames require application- or framework-specific semantics.

Requiring projects to use Dynra decorators, proxy values, or lifecycle types throughout production code would make Dynra difficult to remove and couple application architecture to a development tool. Conversely, attempting to replace every resource without cooperation would be unsafe.

The design must provide Clojure-like interactive development while keeping Dynra optional.

## Goals

1. Dynra is a development dependency, not a production requirement.
2. Removing Dynra and all Dynra adapters does not change application behavior.
3. Ordinary function, method, class, and imported-reference updates require no project changes.
4. Framework-specific lifecycle behavior is supplied by optional framework adapters.
5. Application-specific lifecycle behavior is supplied by optional development-only project adapters.
6. Reconciliation is transactional: prepare and validate replacements before making them visible.
7. Dynra refuses changes it cannot apply safely and explains the exact boundary.
8. Only unavoidable Python, native-runtime, unmanaged-boundary, and external-side-effect limits remain restart-only.

## Non-goals

1. Rewriting currently executing Python or suspended coroutine frames.
2. Migrating arbitrary native-extension memory.
3. Undoing irreversible external effects such as sent messages or completed payments.
4. Guessing lifecycle semantics for unknown resources.
5. Making production application code depend on Dynra types or decorators.

## Coupling rule

A project must pass this test:

```bash
uv remove --dev dynra dynra-stario
uv sync
uv run pytest
uv run stario serve package:bootstrap
```

After removal:

- the application starts normally;
- production behavior is unchanged;
- no production module imports Dynra;
- no domain value is a Dynra proxy;
- no handler requires a Dynra decorator;
- only live-development capabilities disappear.

## Architecture

```text
Application
├── ordinary Python code
├── framework-native startup and shutdown
└── optional framework-neutral lifecycle semantics
          ↑
Development adapters
├── framework adapter
├── project adapter
└── replacement and validation policies
          ↑
Dynra runtime
├── definition patching
├── holder discovery
├── task supervision
├── impact planning
├── transactional reconciliation
└── rollback and diagnostics
```

The application owns behavior and resources. Frameworks own their public lifecycle semantics. Dynra observes and reconciles both through optional adapters.

## Capability levels

| Level | Project changes | Supported behavior |
|---|---|---|
| Transparent core | None | Functions, methods, compatible classes, imports, source healing, definition rollback |
| Framework adapter | Install an optional development package | Routes, middleware, framework tasks, bootstrap-owned resources, server listener preservation |
| Project adapter | Optional development-only configuration | Application schedulers, queue consumers, resource migration, custom validation and compensation |

### Transparent core

Dynra core handles behavior that can be derived safely from Python runtime state:

- preserve compatible function identity;
- repair imported references and known mutable holders;
- update compatible classes and instances;
- discover direct holders and registries;
- detect source changes;
- calculate live-versus-source differences;
- plan definition updates;
- roll back definition updates;
- refuse incompatible updates with evidence.

No application integration is required.

### Framework adapters

Framework adapters encode reusable framework semantics. They belong in separate development packages:

```text
dynra
dynra-stario
dynra-huey
```

Adapters are discovered through package entry points:

```toml
[project.entry-points."dynra.adapters"]
stario = "dynra_stario:adapter"
huey = "dynra_huey:adapter"
```

A Stario adapter can understand:

- bootstrap lifespan;
- route registration and route-table replacement;
- middleware composition;
- handler dependency metadata;
- `app.create_task` ownership;
- active requests and SSE streams;
- application state;
- HTTP listener preservation;
- graceful shutdown ordering.

Application handlers and views remain ordinary Stario code.

### Project adapters

Only application-specific resource semantics require project cooperation. That cooperation lives outside production modules:

```python
# .dynra/geoscope.py

component(
    name="scheduler",
    discover=find_scheduler,
    depends_on=["database", "relay"],
    watches=["geoscope.scheduler.*"],
    prepare=prepare_scheduler,
    replace=replace_scheduler,
    validate=validate_scheduler,
    compensate=restore_scheduler,
)
```

A project adapter may describe:

- how to drain a scheduler or consumer;
- which running tasks may be restarted;
- which resources must retain identity;
- how to migrate state;
- how to validate a replacement;
- how to compensate after failure.

Deleting the adapter reduces live-development capabilities but does not affect the application.

## Core additions

### Stable binding layer

Dynra should provide internal stable references for managed holders without requiring application code to use Dynra proxy types.

```text
router → Dynra-managed holder → current handler implementation
```

When all holders are managed, Dynra may replace an incompatible function object and atomically retarget those holders. When unmanaged holders remain, Dynra must refuse the operation and list them.

This broadens support beyond in-place `__code__` replacement while keeping the binding mechanism internal to Dynra and its adapters.

### Change-impact planning

Before mutation, Dynra produces a deterministic plan:

```text
Changed definitions:
  geoscope.scheduler.scheduler_step

Patched in place:
  scheduler_step

Tasks restarted:
  scheduler-loop, at iteration boundary

Components preserved:
  database, relay, HTTP listener, SSE streams

Rollback available:
  yes
```

Machine-readable output is required for agents and tooling:

```json
{
  "safe": true,
  "requires_process_restart": false,
  "patched": ["geoscope.scheduler.scheduler_step"],
  "restarted": ["scheduler-loop"],
  "preserved": ["database", "relay", "http-listener"],
  "unmanaged_holders": []
}
```

### Runtime dependency graph

Dynra tracks runtime relationships rather than imports alone:

- definitions to holders;
- definitions to tasks currently or potentially invoking them;
- components to their dependencies;
- components to definitions that constructed them;
- instances to live classes;
- configuration values to constructed resources;
- framework registrations to callbacks and middleware.

The graph determines the smallest affected component set and replacement order.

### Component lifecycle protocol

Dynra core defines an adapter-facing protocol, not an application-facing base class:

```python
class ComponentAdapter(Protocol):
    name: str
    dependencies: tuple[str, ...]

    async def discover(self, runtime: RuntimeView) -> object: ...
    async def quiesce(self, component: object) -> None: ...
    async def snapshot(self, component: object) -> object: ...
    async def prepare(self, changes: ChangeSet) -> object: ...
    async def migrate(self, replacement: object, snapshot: object) -> None: ...
    async def validate(self, replacement: object) -> None: ...
    async def commit(self, replacement: object) -> None: ...
    async def resume(self, replacement: object) -> None: ...
    async def stop(self, component: object) -> None: ...
    async def compensate(self, failure: BaseException) -> None: ...
```

Adapters may omit unsupported operations. Missing semantics cause an explicit refusal, not an inferred action.

### Transactional reconciliation

A lifecycle update follows this sequence:

1. Detect changed definitions.
2. Build the runtime impact graph.
3. Generate and expose the reconciliation plan.
4. Quiesce only affected components.
5. Construct replacements while old components remain active where possible.
6. Snapshot and migrate required state.
7. Validate replacements and health checks.
8. Atomically swap managed references.
9. Resume replacement components.
10. Retire old components.
11. Journal completion.

Before the atomic swap, any failure leaves the old system active. After the swap, any failure triggers adapter compensation and reference rollback when safe.

### Supervised tasks and safe points

A suspended coroutine frame cannot be rewritten. Dynra adapters should supervise long-running tasks and restart them at declared boundaries.

Supported policies:

| Policy | Behavior |
|---|---|
| `never` | Change applies after process restart |
| `safe-point` | Finish the current unit and restart before the next unit |
| `immediate` | Cancel and restart immediately |
| `drain` | Stop accepting work, drain active work, then restart |
| `rolling` | Start replacement before retiring the old task |
| `manual` | Produce a plan and wait for operator approval |

Framework adapters may provide safe points around request boundaries, job boundaries, scheduler iterations, and event-consumer acknowledgements. Application code should not need `dynra.safe_point()` calls when the framework already exposes such boundaries.

### Controlled module initialization

Dynra must not blindly replay top-level assignments or side effects. The default remains normal Python semantics. Adapters may explicitly classify bindings as:

- preserved runtime state;
- safe recomputation;
- managed registry;
- managed component;
- restart-only side effect.

Classification belongs in adapter configuration, not mandatory application wrappers.

### State migration

Dynra extends compatible instance updates with transactional migration:

1. Find affected instances.
2. Capture rollback state where supported.
3. Apply adapter- or class-provided migration.
4. Validate all migrated instances.
5. Commit the complete set together.
6. Restore all captured state if one migration fails.

Project classes may optionally implement generic serialization or migration methods that are useful without Dynra. Dynra-specific migration hooks remain optional.

### Framework-neutral lifecycle reuse

Dynra should consume existing application lifecycle methods when available:

```python
class Scheduler:
    async def start(self): ...
    async def drain(self): ...
    async def stop(self): ...
```

These methods benefit graceful shutdown, deployments, tests, and operations independently of Dynra. Dynra adapters map them into reconciliation operations.

### Durable journal and recovery

Every reconciliation records:

- source hashes before and after;
- definition changes;
- component snapshots;
- migration versions;
- prepared replacements;
- the atomic swap point;
- validation results;
- cleanup and compensation results.

After interruption, Dynra reports whether the old system remained active, the new system committed, cleanup is incomplete, or manual recovery is required.

## Safety and degradation

When semantics are unavailable, Dynra applies only the safe subset:

```text
Applied:
  geoscope.views.pages.dashboard_main

Preserved:
  database, relay, active SSE streams

Refused:
  geoscope.bootstrap

Reason:
  bootstrap created unmanaged resources
  no Stario lifecycle adapter is installed

Suggested action:
  install dynra-stario or restart
```

Dynra must never silently leave some holders on old behavior or speculate about resource replacement.

## Unavoidable boundaries

The following remain restart-only or require explicit external recovery:

| Boundary | Reason |
|---|---|
| Currently executing Python frame | Instruction pointer, locals, and block stack belong to its original code object |
| Suspended coroutine frame | It retains the old frame and awaited-object state |
| Native-extension internal layout | Arbitrary native memory cannot be safely inspected or migrated |
| Process-scoped configuration | Some signals, privileges, allocators, and environment state require process recreation |
| Irreversible external side effects | Sent messages, completed payments, and external publications cannot be automatically undone |
| Unmanaged external callers | Their invocation protocol cannot be atomically rewritten |
| Unknown third-party registries | Dynra cannot mutate state it cannot discover or model safely |
| Interpreter, ABI, or binary dependency change | Usually requires interpreter restart |

Dynra must provide an evidence-backed refusal identifying the exact object, holder, frame, or missing semantic contract.

## Packaging

Applications install Dynra and adapters only for development:

```toml
[dependency-groups]
dev = [
    "dynra>=...",
    "dynra-stario>=...",
]
```

Production dependencies remain unchanged.

Adapter activation is automatic in the Dynra runtime and absent from normal application startup:

```bash
uv run dynra
```

No application bootstrap must import Dynra to enable discovery.

## Acceptance criteria

1. A project with no adapters can patch compatible definitions without source changes.
2. Removing Dynra from the environment leaves application tests and startup behavior unchanged.
3. Installing a framework adapter requires no changes to normal handlers, views, or domain models.
4. Project-specific integration can live entirely under `.dynra/` or another development-only path.
5. Every update produces a plan before mutation.
6. A failed preparation or validation leaves the old coherent system serving traffic.
7. A successful component update preserves all unaffected resources.
8. Unmanaged or unsafe changes are refused with actionable evidence.
9. A running task is replaced only according to an explicit supervision policy.
10. A source-clean status guarantees no unresolved live definition or component divergence.
11. The application passes its tests and starts normally after all Dynra packages and adapters are removed.

## Implementation order

| Phase | Deliverable |
|---|---|
| 1 | Rich holder discovery, stable internal binding cells, and machine-readable impact plans |
| 2 | Task supervisors, framework-observed safe points, and restart policies |
| 3 | Adapter protocol and component dependency graph |
| 4 | Transactional prepare, validate, atomic swap, rollback, and journaling |
| 5 | `dynra-stario`: routes, middleware, bootstrap resources, tasks, listener and SSE preservation |
| 6 | Controlled module initialization and managed registries |
| 7 | Transactional instance and component-state migration |
| 8 | Additional framework adapters and crash recovery tooling |

## Decision

Dynra remains an optional development runtime. Transparent language-level patching belongs in Dynra core. Framework lifecycle semantics belong in optional framework adapters. Application-specific lifecycle semantics belong in optional development-only project adapters. Production application code remains ordinary Python.

This design cannot combine zero project knowledge, automatic replacement of every stateful resource, and guaranteed safety. It chooses no production coupling and guaranteed safety. Automatic replacement is available wherever a framework or project adapter supplies the missing semantics; otherwise Dynra refuses the operation explicitly.