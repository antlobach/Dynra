# Dynra TODO

Working checklist. Themes and rationale live in [ROADMAP.md](ROADMAP.md); this file is
the actionable queue. Items are ordered so that each one is doable on its own.

## Done

- [x] **Identity-preserving redefinition.** `pre_execute` retains previous objects, not
      just their ids. `_redefine_in_place` moves new code onto the existing function and
      rebinds the name back to it, so a registry that captured the object at startup
      runs the new code. Verified against a real running web application: a redefined
      route handler kept its `id` and served the new response.
- [x] **Function patch compatibility analysis.** `analyze_function_patch_compatibility`
      mirrors the class-side check and reports closure-layout changes, async/sync
      changes, and non-plain-function inputs.
- [x] **Refusal guard.** `direct_holders` walks `gc.get_referrers`, ignoring module
      namespaces, to find containers holding a function. When an incompatible patch is
      attempted and holders exist, safe mode refuses, restores the previous binding and
      names the holders instead of failing dirty.
- [x] **`function_patch_mode(mode=None)`** runtime command, mirroring `class_patch_mode`.
- [x] **Stop discarding annotations.** `patch_function` no longer overwrites
      `__annotations__` with an empty dict, which silently broke frameworks that resolve
      dependencies from type hints.
- [x] **Project-aware startup.** Discover conventional and configured source roots,
      index importable modules without importing them, and separate discovered, loaded,
      synthetic, and external namespaces.
- [x] **Strict namespace operations.** `require_md`, `create_md`, and `in_md` no longer
      turn import failures into empty synthetic modules or inject commands into
      application module dictionaries.
- [x] **Definition-granular remote evaluation.** Reconcile each top-level definition
      before running the next statement in the same request.


## Next

- [x] **Reconcile decorated redefinitions.** Existing definitions are evaluated without
      re-running registration decorators, retain their canonical identity, and reconcile
      mutable holders of discarded replacements.

- [x] **Support deleting a definition.** `unmap(name)` drops the binding, records a
      restorable snapshot, and reports remaining direct holders.

- [x] **Snapshot and revert.** `revert(target)` and `revert_all()` restore prior function
      and class state.

- [x] **Make multi-definition patches transactional.** A refusal or evaluation failure
      restores every definition touched earlier in the remote evaluation.

## Coverage gaps

Each of these captures a function object the same way a router does, and none is
currently tested. Each needs a test proving the patch either propagates or is refused.

- [ ] Middleware chains registered at startup.
- [ ] `functools.partial` wrapping a patched function.
- [ ] Bound methods stored in a registry.
- [ ] Callbacks held in instance attributes.
- [ ] Functions captured in a closure cell of another function.

## Observability

- [x] **`diverged()`** lists live definitions whose exact evaluated source differs from
      disk and emits unified diffs.
- [x] **Patch log** records applied, refused, reverted, rolled-back, unmapped, and
      persisted operations.
- [x] **Source persistence helper.** `source_patch()` emits the verified diff and
      `persist_source()` writes exact evaluated definition text to disk.

## Concurrency

- [ ] **Define the concurrency model for patches.** In-place code mutation is atomic per
      function, so no caller sees a hybrid, but a multi-definition patch has no such
      guarantee across its parts and nothing pauses in-flight requests. Long-lived
      servers are the primary target, so document the guarantee and add quiescence if
      the documented one is too weak.

## Framework integration

- [ ] **Route add / redefine / delete helper.** Redefinition works now. Add still
      requires locating the live application object by hand, and delete requires
      user-built indirection. Depends on the deletion and decorator items above.

## Documentation

- [x] **README: document runtime commands and project discovery.**
- [x] **README: describe safe function and class patching.**
- [x] **README: state the identity guarantee and transaction boundary.**
- [ ] **Failure taxonomy.** Every refusal message, its cause, and the remedy.

## Project hygiene

- [ ] **Add CI.** There is none. `requires-python` claims 3.10+ while the patch engine
      reads code-object internals that vary across versions; the claim is untested.
- [ ] **Make the suite runnable from a clean checkout.** `test_dynra_client.py`,
      `test_dynra_demo.py` and `test_live_patch_cli.py` need a live server on a fixed
      port. They should spawn their own on an ephemeral port.
- [ ] **Remove or relocate `main.py`.** It is scratch that imports demo packages and
      executes `run_app()` at import time. It is not in `py-modules` and is not an entry
      point.
- [ ] **Decide the status of the demo packages.** `finance_app/`, `dynra_demo/` and
      `test_project/` are fixtures living at the repository root. Move them under a
      fixtures directory or document them as examples.
