# Dynra roadmap

Dynra provides interactive, REPL-driven Python development while preserving live process state. The runtime must favor fidelity over pretending that every Python change can be applied safely.

## Current guarantees

- Project-aware module discovery for repository roots, `src/`, setuptools, Poetry, and Hatch layouts.
- Strict real-module imports and explicit synthetic namespaces.
- Identity-preserving compatible function and class redefinition.
- Propagation through prior imports, registries, closures, partials, and mutable holders.
- Existing-instance migration through `__dynra_update__`.
- Transactional definition batches, patch history, revert, divergence reporting, and source persistence.
- Automatic healing of external top-level function and class edits.
- Per-module generations, source-sync reports, and conflict-safe persistence.
- MCP, interactive terminal, and one-shot client interfaces.
- CI and package builds on Python 3.10 through 3.14.

## Next

### Concurrency

Define quiescence for multi-definition patches in multithreaded applications. Individual function code replacement is atomic under CPython, but a batch does not currently pause callers between definitions.

### Source reconciliation

- Detect stale objects retained outside module namespaces after source deletion.
- Add explicit disk-wins and live-wins conflict resolution commands.
- Evaluate whether content fingerprints are worth the additional clean-path cost over file signatures.

### Failure taxonomy

Document each refusal, the invariant it protects, and the safe recovery path. Include closure-layout changes, sync/async transitions, class layout changes, source conflicts, and failed reconciliation hooks.

### Framework adapters

Keep the core framework-neutral. Add adapters only where a framework requires operations that definition patching cannot express, such as deleting a route from a framework-owned registry.

## Non-goals

- Production hot patching.
- Automatic replay of imports, assignments, or arbitrary module side effects.
- Changing normal Python semantics for copied constants and derived values.
- Pretending native extension state or incompatible class layouts can migrate safely.
- Replacing source control or durable tests.
