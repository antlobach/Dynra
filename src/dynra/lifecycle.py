"""Optional, adapter-facing lifecycle reconciliation.

Nothing in this module is imported by an application unless a development adapter
chooses to use it. Dynra loads adapters only inside its development runtime.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
from dataclasses import asdict, dataclass, field
from enum import Enum
from importlib import metadata
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, runtime_checkable


class ReconciliationRefused(RuntimeError):
    """A change lacks semantics required for a safe live reconciliation."""

    def __init__(self, reasons: Iterable[Mapping[str, Any]]):
        self.reasons = tuple(dict(reason) for reason in reasons)
        message = "; ".join(str(reason.get("reason", reason)) for reason in self.reasons)
        super().__init__(message or "Reconciliation refused")


class TaskPolicy(str, Enum):
    NEVER = "never"
    SAFE_POINT = "safe-point"
    IMMEDIATE = "immediate"
    DRAIN = "drain"
    ROLLING = "rolling"
    MANUAL = "manual"


class BindingKind(str, Enum):
    PRESERVED = "preserved-runtime-state"
    RECOMPUTE = "safe-recomputation"
    REGISTRY = "managed-registry"
    COMPONENT = "managed-component"
    RESTART_ONLY = "restart-only-side-effect"


@dataclass(frozen=True)
class ChangeSet:
    definitions: tuple[str, ...]
    source_before: Mapping[str, str] = field(default_factory=dict)
    source_after: Mapping[str, str] = field(default_factory=dict)

    def hashes(self) -> dict[str, dict[str, str]]:
        def digest(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {
            name: {
                "before": digest(self.source_before.get(name, "")),
                "after": digest(self.source_after.get(name, "")),
            }
            for name in self.definitions
        }


@dataclass
class ManagedHolder:
    """Internal stable cell adapters can retarget atomically."""

    name: str
    current: Any

    def get(self) -> Any:
        return self.current

    def swap(self, replacement: Any) -> Any:
        previous = self.current
        self.current = replacement
        return previous

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.current(*args, **kwargs)


@dataclass(frozen=True)
class ImpactPlan:
    safe: bool
    requires_process_restart: bool
    changed: tuple[str, ...]
    patched: tuple[str, ...] = ()
    restarted: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    affected_components: tuple[str, ...] = ()
    unmanaged_holders: tuple[str, ...] = ()
    refusals: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeView:
    components: Mapping[str, Any]
    holders: Mapping[str, ManagedHolder]
    classifications: Mapping[str, BindingKind]


@runtime_checkable
class ComponentAdapter(Protocol):
    name: str
    dependencies: tuple[str, ...]
    watches: tuple[str, ...]

    async def discover(self, runtime: RuntimeView) -> object: ...
    async def prepare(self, changes: ChangeSet) -> object: ...
    async def commit(self, replacement: object) -> None: ...


@dataclass
class ComponentRegistration:
    adapter: Any
    instance: Any = None
    holder: ManagedHolder | None = None

    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return tuple(getattr(self.adapter, "dependencies", ()))

    @property
    def watches(self) -> tuple[str, ...]:
        return tuple(getattr(self.adapter, "watches", ()))


class RuntimeGraph:
    def __init__(self) -> None:
        self._components: dict[str, ComponentRegistration] = {}
        self._definition_holders: dict[str, set[str]] = {}
        self._unmanaged_holders: dict[str, set[str]] = {}

    @property
    def components(self) -> Mapping[str, ComponentRegistration]:
        return dict(self._components)

    def register_component(self, registration: ComponentRegistration) -> None:
        if registration.name in self._components:
            raise ValueError(f"Duplicate component: {registration.name}")
        self._components[registration.name] = registration
        self.order(self._components)

    def track_holder(self, definition: str, holder: str, *, managed: bool) -> None:
        target = self._definition_holders if managed else self._unmanaged_holders
        target.setdefault(definition, set()).add(holder)

    def order(self, names: Iterable[str]) -> tuple[str, ...]:
        requested = set(names)
        expanded = set(requested)
        pending = list(requested)
        while pending:
            name = pending.pop()
            if name not in self._components:
                raise ValueError(f"Unknown component dependency: {name}")
            for dependency in self._components[name].dependencies:
                if dependency not in expanded:
                    expanded.add(dependency)
                    pending.append(dependency)
        ordered: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                cycle = " -> ".join((*visiting, name))
                raise ValueError(f"Component dependency cycle: {cycle}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in sorted(self._components[name].dependencies):
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(name)

        for name in sorted(expanded):
            visit(name)
        return tuple(ordered)

    def affected(self, definitions: Iterable[str]) -> tuple[str, ...]:
        changed = tuple(definitions)
        direct = {
            name
            for name, registration in self._components.items()
            if any(
                definition == watch or definition.startswith(watch.rstrip("*") )
                for definition in changed
                for watch in registration.watches
            )
        }
        dependents = True
        while dependents:
            before = len(direct)
            direct.update(
                name
                for name, registration in self._components.items()
                if set(registration.dependencies) & direct
            )
            dependents = len(direct) != before
        return self.order(direct) if direct else ()

    def plan(self, changes: ChangeSet) -> ImpactPlan:
        unmanaged = tuple(
            sorted(
                f"{definition}: {holder}"
                for definition in changes.definitions
                for holder in self._unmanaged_holders.get(definition, ())
            )
        )
        affected = self.affected(changes.definitions)
        refusals = tuple(
            {
                "definition": item.split(": ", 1)[0],
                "holder": item.split(": ", 1)[1],
                "reason": "unmanaged holder cannot be atomically retargeted",
                "suggested_action": "install an adapter or restart",
            }
            for item in unmanaged
        )
        return ImpactPlan(
            safe=not unmanaged,
            requires_process_restart=bool(unmanaged),
            changed=changes.definitions,
            patched=changes.definitions if not unmanaged else (),
            affected_components=affected,
            preserved=tuple(sorted(set(self._components) - set(affected))),
            unmanaged_holders=unmanaged,
            refusals=refusals,
        )


@dataclass
class JournalEntry:
    sequence: int
    status: str
    plan: Mapping[str, Any]
    source_hashes: Mapping[str, Any]
    phases: list[Mapping[str, Any]] = field(default_factory=list)
    recovery: str = "old-system-active"


async def _invoke(target: Any, operation: str, *args: Any, required: bool = False) -> Any:
    function = getattr(target, operation, None)
    if function is None:
        if required:
            raise ReconciliationRefused(({
                "component": target.name,
                "operation": operation,
                "reason": f"component adapter does not provide {operation}",
                "suggested_action": "add adapter semantics or restart",
            },))
        return None
    value = function(*args)
    return await value if inspect.isawaitable(value) else value


class LifecycleRuntime:
    """Development-only coordinator for adapter-managed resources."""

    def __init__(self) -> None:
        self.graph = RuntimeGraph()
        self.holders: dict[str, ManagedHolder] = {}
        self.classifications: dict[str, BindingKind] = {}
        self.journal: list[JournalEntry] = []
        self.task_supervisors: dict[str, TaskSupervisor] = {}

    def register_holder(self, name: str, value: Any) -> ManagedHolder:
        holder = ManagedHolder(name, value)
        self.holders[name] = holder
        return holder

    def classify(self, binding: str, kind: BindingKind | str) -> None:
        self.classifications[binding] = BindingKind(kind)

    def register_component(self, adapter: Any, instance: Any = None) -> None:
        holder = self.register_holder(f"component:{adapter.name}", instance)
        self.graph.register_component(ComponentRegistration(adapter, instance, holder))

    def impact_plan(self, changes: ChangeSet) -> ImpactPlan:
        plan = self.graph.plan(changes)
        restart_bindings = tuple(
            name
            for name, kind in self.classifications.items()
            if kind is BindingKind.RESTART_ONLY
            and any(change == name or change.startswith(name + ".") for change in changes.definitions)
        )
        if not restart_bindings:
            return plan
        refusals = plan.refusals + tuple(
            {
                "definition": name,
                "reason": "binding is classified as restart-only side effect",
                "suggested_action": "restart",
            }
            for name in restart_bindings
        )
        return ImpactPlan(**{
            **plan.as_dict(),
            "safe": False,
            "requires_process_restart": True,
            "refusals": refusals,
        })

    def journal_view(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(copy.deepcopy(asdict(entry)) for entry in self.journal)

    async def reconcile(self, changes: ChangeSet) -> Mapping[str, Any]:
        plan = self.impact_plan(changes)
        entry = JournalEntry(
            sequence=len(self.journal) + 1,
            status="planned",
            plan=plan.as_dict(),
            source_hashes=changes.hashes(),
        )
        self.journal.append(entry)
        if not plan.safe:
            entry.status = "refused"
            entry.phases.append({"phase": "refused", "reasons": plan.refusals})
            raise ReconciliationRefused(plan.refusals)

        registrations = [self.graph.components[name] for name in plan.affected_components]
        prepared: dict[str, Any] = {}
        snapshots: dict[str, Any] = {}
        swapped: list[tuple[ComponentRegistration, Any]] = []
        try:
            for registration in reversed(registrations):
                await _invoke(registration.adapter, "quiesce", registration.instance)
            entry.phases.append({"phase": "quiesced", "components": plan.affected_components})
            for registration in registrations:
                adapter = registration.adapter
                snapshots[registration.name] = await _invoke(
                    adapter, "snapshot", registration.instance
                )
                replacement = await _invoke(adapter, "prepare", changes, required=True)
                await _invoke(adapter, "migrate", replacement, snapshots[registration.name])
                await _invoke(adapter, "validate", replacement, required=True)
                prepared[registration.name] = replacement
            entry.phases.append({"phase": "prepared", "components": tuple(prepared)})
            for registration in registrations:
                replacement = prepared[registration.name]
                old = registration.holder.swap(replacement)
                swapped.append((registration, old))
                registration.instance = replacement
            entry.phases.append({"phase": "atomic-swap", "components": tuple(prepared)})
            entry.recovery = "new-system-committed"
            for registration in registrations:
                replacement = prepared[registration.name]
                await _invoke(registration.adapter, "commit", replacement, required=True)
                await _invoke(registration.adapter, "resume", replacement)
            for registration, old in reversed(swapped):
                await _invoke(registration.adapter, "stop", old)
            entry.status = "committed"
            entry.phases.append({"phase": "cleanup-complete"})
            return copy.deepcopy(asdict(entry))
        except BaseException as error:
            after_swap = bool(swapped)
            compensation_errors = []
            for registration, old in reversed(swapped):
                try:
                    registration.holder.swap(old)
                    registration.instance = old
                    await _invoke(registration.adapter, "compensate", error)
                    await _invoke(registration.adapter, "resume", old)
                except BaseException as compensation_error:
                    compensation_errors.append(
                        f"{registration.name}: {type(compensation_error).__name__}: {compensation_error}"
                    )
            for registration in reversed(registrations):
                if registration.name not in prepared:
                    try:
                        await _invoke(registration.adapter, "resume", registration.instance)
                    except BaseException as resume_error:
                        compensation_errors.append(
                            f"{registration.name}: {type(resume_error).__name__}: {resume_error}"
                        )
            entry.status = "manual-recovery-required" if compensation_errors else "rolled-back"
            entry.recovery = (
                "manual-recovery-required"
                if compensation_errors
                else "old-system-restored"
                if after_swap
                else "old-system-active"
            )
            entry.phases.append({
                "phase": "compensation" if after_swap else "pre-swap-failure",
                "error": f"{type(error).__name__}: {error}",
                "errors": tuple(compensation_errors),
            })
            raise


class TaskSupervisor:
    """Applies only an explicit restart policy to an adapter-owned task."""

    def __init__(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
        policy: TaskPolicy | str,
        *,
        drain: Callable[[], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self.name = name
        self.factory = factory
        self.policy = TaskPolicy(policy)
        self.drain_hook = drain
        self.task: asyncio.Task[Any] | None = None
        self._safe_point = asyncio.Event()

    def start(self) -> asyncio.Task[Any]:
        if self.task is not None and not self.task.done():
            raise RuntimeError(f"Task {self.name!r} is already running")
        self._safe_point.clear()
        self.task = asyncio.create_task(self.factory(), name=self.name)
        return self.task

    def mark_safe_point(self) -> None:
        self._safe_point.set()

    async def restart(self, *, approved: bool = False) -> None:
        if self.policy is TaskPolicy.NEVER:
            raise ReconciliationRefused(({
                "task": self.name,
                "reason": "task policy is never",
                "suggested_action": "restart process",
            },))
        if self.policy is TaskPolicy.MANUAL and not approved:
            raise ReconciliationRefused(({
                "task": self.name,
                "reason": "task policy requires operator approval",
                "suggested_action": "approve the reconciliation plan",
            },))
        old = self.task
        if self.policy is TaskPolicy.SAFE_POINT:
            await self._safe_point.wait()
        elif self.policy is TaskPolicy.DRAIN and self.drain_hook is not None:
            value = self.drain_hook()
            if inspect.isawaitable(value):
                await value
        if self.policy is TaskPolicy.ROLLING:
            replacement = self.start_replacement()
            await asyncio.sleep(0)
            if old is not None and not old.done():
                old.cancel()
            self.task = replacement
            return
        if old is not None and not old.done():
            old.cancel()
            try:
                await old
            except asyncio.CancelledError:
                pass
        self.start()

    def start_replacement(self) -> asyncio.Task[Any]:
        return asyncio.create_task(self.factory(), name=self.name)


def migrate_instances(
    instances: Iterable[Any],
    migrate: Callable[[Any], Any],
    validate: Callable[[Any], Any] | None = None,
) -> None:
    """Migrate a set atomically when instance dictionaries are restorable."""
    selected = tuple(instances)
    snapshots = []
    for instance in selected:
        if not hasattr(instance, "__dict__"):
            raise ReconciliationRefused(({
                "instance": repr(instance),
                "reason": "instance has no restorable state dictionary",
            },))
        snapshots.append(copy.deepcopy(instance.__dict__))
    try:
        for instance in selected:
            migrate(instance)
            if validate is not None:
                validate(instance)
    except BaseException:
        for instance, snapshot in zip(selected, snapshots):
            instance.__dict__.clear()
            instance.__dict__.update(snapshot)
        raise


def discover_adapters(group: str = "dynra.adapters") -> tuple[Any, ...]:
    """Load development adapters from package entry points deterministically."""
    entry_points = metadata.entry_points()
    selected = entry_points.select(group=group) if hasattr(entry_points, "select") else entry_points.get(group, ())
    adapters = []
    for entry_point in sorted(selected, key=lambda item: item.name):
        loaded = entry_point.load()
        adapters.append(loaded() if callable(loaded) and not hasattr(loaded, "name") else loaded)
    return tuple(adapters)
