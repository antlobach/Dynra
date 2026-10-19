# Dynra roadmap

## Thesis

Dynra's value is not that it can change code in a running process. `exec` does that.
The value is that an agent can **trust the result** without restarting to check.

An agent editing a live process is making a claim: *the behaviour I just verified is
the behaviour this process will keep having.* That claim holds only if the runtime
guarantees three properties.

**Fidelity.** A patch reaches every holder of the old code, or it refuses. There is no
third outcome where some callers run new code and others run old code. Partial
application is worse than no application, because the agent verifies through one path
while traffic takes another.

**Reversibility.** No patch can leave the process in a state that no further patch can
fix. An agent explores by trying things; exploration is only rational if mistakes are
cheap.

**Observability.** The agent can always ask how the live process differs from the
source on disk. Without this, a long session degrades into folklore, and the final
source edit becomes a guess about what was actually proven.

Everything below is ordered by how much it moves those three properties. Version
numbers are sequencing, not dates.

---

## v0.1 — Project awareness

**Repository module discovery.** *Done.* Dynra reads packaging configuration and
conventional `src/` layouts, activates source roots, and indexes modules without
importing them. Runtime listings distinguish discovered project modules, loaded project
modules, explicit synthetic namespaces, and external modules.

**Strict namespace operations.** *Done.* `require_md` imports, `create_md` creates, and
`in_md` switches. Import failures are no longer converted into empty namespaces, and
Dynra commands no longer mutate imported application module dictionaries.

## v0.2 — Fidelity

The largest correctness gap: a patch that appears to work but never reaches the object
a framework is actually calling.

**Identity-preserving redefinition.** *Done.* New code is moved onto the existing
function object and the name is rebound to it, so exactly one object per name exists
for the life of the process. Registry-captured callables, closure cells, partials and
prior importers all observe every edit. This is the mechanism that makes the other two
properties worth having.

**Refusal instead of silent divergence.** *Done.* Closure-layout and async/sync changes
cannot be applied in place. When one is attempted and something holds the object
directly, the patch is refused and the holders are named, rather than rebinding and
leaving a registry on dead code.

**Decorator reconciliation.** *Done.* Existing decorated definitions are evaluated
without re-running registration decorators, then their code is moved onto the canonical
object. Mutable holders of a discarded replacement are reconciled to that canonical
identity. This permits source-shaped Flask handler redefinition without duplicate route
registration.

**Deletion.** *Done.* `unmap(name)` removes a binding, records an undo snapshot, and
reports direct holders that remain alive outside the module.

**Coverage for the remaining capture idioms.** Middleware chains, `functools.partial`,
bound methods stored in registries, and callbacks held in instance attributes all
capture function objects the same way a router does. Each needs a test proving it
either updates or refuses.

## v0.3 — Reversibility

**Snapshot and revert.** *Done.* Every definition change retains restorable function or
class state. `revert(target)` and `revert_all()` undo live edits without restarting.

**Transactional multi-definition patches.** *Done.* Remote evaluations reconcile each
top-level definition before the next statement. A refusal or execution failure restores
all definitions touched by that evaluation. Arbitrary external side effects remain
outside the transaction boundary.

## v0.4 — Observability

**Divergence report.** *Done.* `diverged()` compares exact evaluated definition source
with the corresponding top-level definition on disk and returns unified diffs.

**Patch log.** *Done.* Applied, refused, reverted, unmapped, rolled-back, and persisted
operations are recorded in order.

**Source persistence helper.** *Done.* `source_patch()` emits unified diffs and
`persist_source()` writes the exact evaluated definition text back to its source file.

## v0.5 — Concurrency

**Quiescence for patches.** Nothing prevents a patch landing while a request executes on
another thread. In-place code mutation is atomic per function, so a caller sees either
old or new code and never a hybrid, but a multi-definition patch has no such guarantee
across its parts. Long-lived server processes are the primary target, so this needs an
explicit model rather than an assumption.

## v0.6 — Framework integration

**First-class route operations.** Add, redefine and delete for web routes, built on the
deletion and decorator work above. Redefinition already works; add requires reaching
the live application object, and delete requires indirection the user must build by
hand. A supported helper removes the last sharp edge on the most common agent task.

## v1.0 — Hardening

**Continuous integration.** There is none. `requires-python` claims 3.10+, and the patch
engine reads code-object internals whose behaviour varies across versions. A test
matrix over supported interpreters is a prerequisite for that claim.

**Deterministic test suite.** Several tests need a live server on a fixed port and fail
in a clean checkout. They should spawn their own instance on an ephemeral port.

**Documented failure taxonomy.** Every refusal an agent can hit, why it happens, and the
remedy. Refusals are a feature, but only if the agent can act on them without guessing.

---

## Explicit non-goals

**Not a production hot-patch mechanism.** Dynra executes arbitrary code and targets
trusted local development. Shipping live patches to production is a different problem
with different safety requirements.

**Not a replacement for tests or source control.** A live patch proves behaviour in one
process. The proven change still gets written to source and covered by durable checks.

**Not a general-purpose reloader.** Whole-module reload re-runs import side effects and
resets state, which is the problem Dynra exists to avoid. Granularity stays at the
definition.

**Not full parity with an image-based language.** Runtimes with language-level
indirection redefine anything at any time because every call goes through a mutable
reference. Python binds names to values, so Dynra emulates that with in-place mutation
and must refuse where the emulation breaks. Closing the gaps above narrows the
difference; it does not erase it, and pretending otherwise would break the fidelity
property that makes the tool useful.
