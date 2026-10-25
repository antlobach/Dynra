"""Optional, adapter-facing lifecycle reconciliation for development runtimes."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import inspect
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from importlib import metadata
from pathlib import Path
from types import MappingProxyType
from typing import (
    Any,
    Protocol,
    runtime_checkable,
)


class ReconciliationRefused(RuntimeError):
    def __init__(self, reasons: Iterable[Mapping[str, Any]]):
        self.reasons = tuple(dict(reason) for reason in reasons)
        super().__init__(
            "; ".join(str(r.get("reason", r)) for r in self.reasons)
            or "Reconciliation refused"
        )


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
        digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {
            name: {
                "before": digest(self.source_before.get(name, "")),
                "after": digest(self.source_after.get(name, "")),
            }
            for name in self.definitions
        }


class _GenerationStore:
    def __init__(self) -> None:
        self._snapshot: Mapping[str, Any] = MappingProxyType({})
        self._lock = threading.Lock()
        self.generation = 0

    def snapshot(self) -> Mapping[str, Any]:
        return self._snapshot

    def replace(self, changes: Mapping[str, Any]) -> tuple[Mapping[str, Any], int]:
        with self._lock:
            previous = self._snapshot
            updated = dict(previous)
            updated.update(changes)
            self._snapshot = MappingProxyType(updated)
            self.generation += 1
            return previous, self.generation

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        with self._lock:
            self._snapshot = MappingProxyType(dict(snapshot))
            self.generation += 1


class ManagedHolder:
    """Stable reference backed by one atomically replaced generation snapshot."""

    def __init__(self, name: str, current: Any, store: _GenerationStore | None = None):
        self.name = name
        self._store = store or _GenerationStore()
        if name not in self._store.snapshot():
            self._store.replace({name: current})

    @property
    def current(self) -> Any:
        return self.get()

    @current.setter
    def current(self, value: Any) -> None:
        self._store.replace({self.name: value})

    def get(self) -> Any:
        return self._store.snapshot()[self.name]

    def swap(self, replacement: Any) -> Any:
        previous = self.get()
        self._store.replace({self.name: replacement})
        return previous

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.get()(*args, **kwargs)


@dataclass(frozen=True)
class ImpactPlan:
    safe: bool
    requires_process_restart: bool
    changed: tuple[str, ...]
    patched: tuple[str, ...] = ()
    restarted: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    affected_components: tuple[str, ...] = ()
    affected_bindings: tuple[str, ...] = ()
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


@dataclass
class BindingRegistration:
    name: str
    kind: BindingKind
    watches: tuple[str, ...]
    holder: ManagedHolder
    prepare: Callable[[ChangeSet], Any] | None = None
    validate: Callable[[Any], Any] | None = None
    component: str | None = None


class RuntimeGraph:
    def __init__(self) -> None:
        self._components: dict[str, ComponentRegistration] = {}
        self._managed_holders: dict[str, set[str]] = {}
        self._unmanaged_holders: dict[str, set[str]] = {}

    @property
    def components(self) -> Mapping[str, ComponentRegistration]:
        return dict(self._components)

    def register_component(
        self, registration: ComponentRegistration, *, validate: bool = True
    ) -> None:
        if registration.name in self._components:
            raise ValueError(f"Duplicate component: {registration.name}")
        self._components[registration.name] = registration
        if not validate:
            return
        try:
            self.order(self._components, include_dependencies=True)
        except Exception:
            self._components.pop(registration.name, None)
            raise

    def track_holder(self, definition: str, holder: str, *, managed: bool) -> None:
        (self._managed_holders if managed else self._unmanaged_holders).setdefault(
            definition, set()
        ).add(holder)

    def replace_holder_evidence(
        self,
        definition: str,
        *,
        managed: Iterable[str] = (),
        unmanaged: Iterable[str] = (),
    ) -> None:
        self._managed_holders[definition] = set(managed)
        self._unmanaged_holders[definition] = set(unmanaged)

    def order(
        self, names: Iterable[str], *, include_dependencies: bool = True
    ) -> tuple[str, ...]:
        selected = set(names)
        if include_dependencies:
            pending = list(selected)
            while pending:
                name = pending.pop()
                if name not in self._components:
                    raise ValueError(f"Unknown component dependency: {name}")
                for dependency in self._components[name].dependencies:
                    if dependency not in selected:
                        selected.add(dependency)
                        pending.append(dependency)
        ordered, visiting, visited = [], [], set()

        def visit(name: str) -> None:
            if name not in self._components:
                raise ValueError(f"Unknown component dependency: {name}")
            if name in visiting:
                raise ValueError(
                    "Component dependency cycle: " + " -> ".join((*visiting, name))
                )
            if name in visited:
                return
            visiting.append(name)
            for dependency in sorted(self._components[name].dependencies):
                if include_dependencies or dependency in selected:
                    visit(dependency)
            visiting.pop()
            visited.add(name)
            ordered.append(name)

        for name in sorted(selected):
            visit(name)
        return tuple(ordered)

    @staticmethod
    def _matches(definition: str, watch: str) -> bool:
        return definition == watch or (
            watch.endswith("*") and definition.startswith(watch[:-1])
        )

    def affected(self, definitions: Iterable[str]) -> tuple[str, ...]:
        changed = tuple(definitions)
        affected = {
            name
            for name, reg in self._components.items()
            if any(self._matches(d, w) for d in changed for w in reg.watches)
        }
        while True:
            expanded = affected | {
                name
                for name, reg in self._components.items()
                if set(reg.dependencies) & affected
            }
            if expanded == affected:
                break
            affected = expanded
        return self.order(affected, include_dependencies=False) if affected else ()

    def holder_evidence(self, definitions: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                f"{definition}: {holder}"
                for definition in definitions
                for holder in self._unmanaged_holders.get(definition, ())
            )
        )

    def plan(self, changes: ChangeSet) -> ImpactPlan:
        unmanaged = self.holder_evidence(changes.definitions)
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
            not unmanaged,
            bool(unmanaged),
            changes.definitions,
            changes.definitions if not unmanaged else (),
            preserved=tuple(sorted(set(self._components) - set(affected))),
            affected_components=affected,
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
    transaction_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class DurableJournal:
    def __init__(self, directory: str | Path | None):
        self.directory = Path(directory).resolve() if directory else None
        self.entries: list[JournalEntry] = []
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.directory.glob("lifecycle-*.json")):
                try:
                    entry = JournalEntry(**json.loads(path.read_text()))
                    if entry.status in {"planned", "prepared"}:
                        entry.status = "recovery-required"
                        entry.recovery = "old-system-active"
                    elif entry.status == "swapped":
                        entry.status = "recovery-required"
                        entry.recovery = "cleanup-incomplete"
                    self.entries.append(entry)
                except (OSError, ValueError, TypeError):
                    continue

    def append(self, entry: JournalEntry) -> None:
        self.entries.append(entry)
        self.persist(entry)

    def persist(self, entry: JournalEntry) -> None:
        if not self.directory:
            return
        target = self.directory / (
            f"lifecycle-{entry.sequence:08d}-{entry.transaction_id}.json"
        )
        fd, temporary = tempfile.mkstemp(prefix=target.name, dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(entry), stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def prune(self, keep: int = 100) -> None:
        if keep < 0:
            raise ValueError("keep must be non-negative")
        completed = [
            entry
            for entry in self.entries
            if entry.status in {"committed", "rolled-back", "refused"}
        ]
        removable = completed if keep == 0 else completed[:-keep]
        for entry in removable:
            if self.directory:
                (
                    self.directory
                    / f"lifecycle-{entry.sequence:08d}-{entry.transaction_id}.json"
                ).unlink(missing_ok=True)
            self.entries.remove(entry)


async def _invoke(
    target: Any, operation: str, *args: Any, required: bool = False
) -> Any:
    function = getattr(target, operation, None)
    if function is None:
        if required:
            raise ReconciliationRefused(
                (
                    {
                        "component": target.name,
                        "operation": operation,
                        "reason": f"component adapter does not provide {operation}",
                        "suggested_action": "add adapter semantics or restart",
                    },
                )
            )
        return None
    value = function(*args)
    return await value if inspect.isawaitable(value) else value


class LifecycleRuntime:
    def __init__(
        self, *, adapters: Iterable[Any] = (), journal_dir: str | Path | None = None
    ) -> None:
        self.graph = RuntimeGraph()
        self._store = _GenerationStore()
        self.holders: dict[str, ManagedHolder] = {}
        self.classifications: dict[str, BindingKind] = {}
        self.bindings: dict[str, BindingRegistration] = {}
        self.task_supervisors: dict[str, TaskSupervisor] = {}
        self._adapters = tuple(adapters)
        self._activated = False
        self._journal = DurableJournal(journal_dir)
        self.journal = self._journal.entries

    def snapshot(self) -> Mapping[str, Any]:
        return self._store.snapshot()

    def atomic_swap(self, values: Mapping[str, Any]) -> Mapping[str, Any]:
        previous, _ = self._store.replace(values)
        return previous

    def register_holder(self, name: str, value: Any) -> ManagedHolder:
        if name in self.holders:
            raise ValueError(f"Duplicate holder: {name}")
        holder = ManagedHolder(name, value, self._store)
        self.holders[name] = holder
        return holder

    def classify(self, binding: str, kind: BindingKind | str) -> None:
        self.classifications[binding] = BindingKind(kind)

    def register_binding(
        self,
        name: str,
        value: Any,
        kind: BindingKind | str,
        *,
        watches: Iterable[str] = (),
        prepare=None,
        validate=None,
        component=None,
    ) -> ManagedHolder:
        kind = BindingKind(kind)
        holder = self.register_holder(f"binding:{name}", value)
        self.classifications[name] = kind
        self.bindings[name] = BindingRegistration(
            name, kind, tuple(watches), holder, prepare, validate, component
        )
        return holder

    def register_component(self, adapter: Any, instance: Any = None) -> None:
        holder = self.register_holder(f"component:{adapter.name}", instance)
        try:
            self.graph.register_component(
                ComponentRegistration(adapter, instance, holder)
            )
        except Exception:
            self.holders.pop(holder.name, None)
            self.atomic_swap({holder.name: None})
            raise

    async def activate_adapters(self) -> tuple[str, ...]:
        if self._activated:
            return tuple(sorted(self.graph.components))
        existing = self.graph.components
        view = RuntimeView(
            {name: registration.instance for name, registration in existing.items()},
            {
                name: holder
                for name, holder in self.holders.items()
                if name.startswith("component:")
            },
            dict(self.classifications),
        )
        discovered = []
        for adapter in self._adapters:
            components = getattr(adapter, "components", (adapter,))
            for component in components:
                instance = await _invoke(component, "discover", view, required=True)
                discovered.append((component, instance))
                holder_name = f"component:{component.name}"
                staged_holder = ManagedHolder(holder_name, instance)
                view = RuntimeView(
                    {**view.components, component.name: instance},
                    {**view.holders, holder_name: staged_holder},
                    dict(self.classifications),
                )
        staged = RuntimeGraph()
        for registration in existing.values():
            staged.register_component(registration, validate=False)
        for component, instance in discovered:
            staged.register_component(
                ComponentRegistration(component, instance), validate=False
            )
        staged.order(staged.components, include_dependencies=True)
        replacements = {
            f"component:{component.name}": instance
            for component, instance in discovered
        }
        self.atomic_swap(replacements)
        for component, instance in discovered:
            holder_name = f"component:{component.name}"
            holder = ManagedHolder(holder_name, instance, self._store)
            self.holders[holder_name] = holder
            staged._components[component.name].holder = holder
        self.graph = staged
        self._activated = True
        return tuple(name for name, _ in ((c.name, i) for c, i in discovered))

    async def plan(self, changes: ChangeSet) -> ImpactPlan:
        if self._adapters and not self._activated:
            await self.activate_adapters()
        return self.impact_plan(changes)

    def register_task(self, supervisor: TaskSupervisor) -> None:
        if supervisor.name in self.task_supervisors:
            raise ValueError(f"Duplicate task: {supervisor.name}")
        self.task_supervisors[supervisor.name] = supervisor

    def impact_plan(self, changes: ChangeSet) -> ImpactPlan:
        plan = self.graph.plan(changes)
        refusals = list(plan.refusals)
        if self._adapters and not self._activated:
            refusals.append(
                {
                    "reason": "lifecycle adapters are not activated",
                    "suggested_action": "request a lifecycle plan before mutation",
                }
            )
        restart = plan.requires_process_restart
        affected_bindings = tuple(
            sorted(
                name
                for name, binding in self.bindings.items()
                if any(
                    RuntimeGraph._matches(d, w)
                    for d in changes.definitions
                    for w in binding.watches
                )
            )
        )
        restarted = tuple(
            sorted(
                name
                for name, task in self.task_supervisors.items()
                if any(
                    RuntimeGraph._matches(d, w)
                    for d in changes.definitions
                    for w in task.watches
                )
            )
        )
        for name in plan.affected_components:
            adapter = self.graph.components[name].adapter
            for operation in (
                "quiesce",
                "prepare",
                "validate",
                "commit",
                "compensate",
                "resume",
            ):
                if not callable(getattr(adapter, operation, None)):
                    refusals.append(
                        {
                            "component": name,
                            "operation": operation,
                            "reason": (
                                f"component adapter does not provide {operation}"
                            ),
                            "suggested_action": "add adapter semantics or restart",
                        }
                    )
        for name in affected_bindings:
            if self.bindings[name].kind is BindingKind.RESTART_ONLY:
                restart = True
                refusals.append(
                    {
                        "binding": name,
                        "reason": "binding is classified as restart-only side effect",
                        "suggested_action": "restart",
                    }
                )
            binding = self.bindings[name]
            if binding.kind in {BindingKind.RECOMPUTE, BindingKind.REGISTRY}:
                if binding.prepare is None:
                    refusals.append(
                        {
                            "binding": name,
                            "operation": "prepare",
                            "reason": "binding does not provide safe preparation",
                            "suggested_action": "add binding semantics or restart",
                        }
                    )
                if binding.kind is BindingKind.REGISTRY and binding.validate is None:
                    refusals.append(
                        {
                            "binding": name,
                            "operation": "validate",
                            "reason": "managed registry does not provide validation",
                            "suggested_action": "add binding semantics or restart",
                        }
                    )
            elif binding.kind is BindingKind.COMPONENT and (
                not binding.component
                or binding.component not in plan.affected_components
            ):
                refusals.append(
                    {
                        "binding": name,
                        "reason": "component binding has no affected adapter",
                        "suggested_action": "watch the owning component or restart",
                    }
                )
        for name, kind in self.classifications.items():
            if (
                kind is BindingKind.RESTART_ONLY
                and name not in self.bindings
                and any(
                    definition == name or definition.startswith(name + ".")
                    for definition in changes.definitions
                )
            ):
                restart = True
                refusals.append(
                    {
                        "definition": name,
                        "reason": "binding is classified as restart-only side effect",
                        "suggested_action": "restart",
                    }
                )
        for name in restarted:
            task = self.task_supervisors[name]
            if task.policy is TaskPolicy.NEVER:
                refusals.append(
                    {
                        "task": name,
                        "reason": "task policy is never",
                        "suggested_action": "restart process",
                    }
                )
            elif task.policy is TaskPolicy.MANUAL:
                refusals.append(
                    {
                        "task": name,
                        "reason": "task policy requires operator approval",
                        "suggested_action": "approve plan",
                    }
                )
            elif task.policy is TaskPolicy.DRAIN and task.drain_hook is None:
                refusals.append(
                    {
                        "task": name,
                        "reason": "drain policy has no drain hook",
                        "suggested_action": "configure task drain semantics",
                    }
                )
            elif task.policy is TaskPolicy.ROLLING and task.readiness is None:
                refusals.append(
                    {
                        "task": name,
                        "reason": "rolling policy has no readiness check",
                        "suggested_action": "configure task readiness semantics",
                    }
                )
        return ImpactPlan(
            not refusals,
            restart,
            plan.changed,
            plan.patched if not refusals else (),
            restarted,
            plan.preserved,
            plan.affected_components,
            affected_bindings,
            plan.unmanaged_holders,
            tuple(refusals),
        )

    def journal_view(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(copy.deepcopy(asdict(entry)) for entry in self.journal)

    def prune_journal(self, keep: int = 100) -> tuple[Mapping[str, Any], ...]:
        self._journal.prune(keep)
        return self.journal_view()

    async def reconcile(
        self,
        changes: ChangeSet,
        *,
        approved: bool = False,
        apply: Callable[[], Any] | None = None,
        rollback: Callable[[], Any] | None = None,
    ) -> Mapping[str, Any]:
        if apply is not None and rollback is None:
            raise ValueError("definition apply requires a rollback callback")
        if self._adapters and not self._activated:
            await self.activate_adapters()
        plan = self.impact_plan(changes)
        if approved and plan.refusals:
            filtered = tuple(
                r
                for r in plan.refusals
                if not (r.get("task") and "approval" in r.get("reason", ""))
            )
            plan = ImpactPlan(
                **{**plan.as_dict(), "safe": not filtered, "refusals": filtered}
            )
        preflight_refusals = list(plan.refusals)
        if len(plan.affected_components) > 1 or apply is not None:
            for name in plan.affected_components:
                adapter = self.graph.components[name].adapter
                for operation in ("quiesce", "resume"):
                    if not callable(getattr(adapter, operation, None)):
                        preflight_refusals.append(
                            {
                                "component": name,
                                "operation": operation,
                                "reason": (
                                    f"component adapter does not provide {operation}"
                                ),
                                "suggested_action": (
                                    "add atomic quiescence semantics or restart"
                                ),
                            }
                        )
        if tuple(preflight_refusals) != plan.refusals:
            plan = ImpactPlan(
                **{
                    **plan.as_dict(),
                    "safe": False,
                    "patched": (),
                    "refusals": tuple(preflight_refusals),
                }
            )
        entry = JournalEntry(
            len(self.journal) + 1, "planned", plan.as_dict(), changes.hashes()
        )
        self._journal.append(entry)
        if not plan.safe:
            entry.status = "refused"
            entry.phases.append({"phase": "refused", "reasons": plan.refusals})
            self._journal.persist(entry)
            raise ReconciliationRefused(plan.refusals)
        regs = [self.graph.components[name] for name in plan.affected_components]
        prepared_components, prepared_bindings, quiesced = {}, {}, []
        prepared_publications: dict[str, Any] = {}
        prepared_tasks: dict[str, PreparedTaskRestart] = {}
        committed_tasks: set[str] = set()
        old_snapshot = self.snapshot()
        swapped = False
        definitions_applied = False
        try:
            for reg in reversed(regs):
                await _invoke(
                    reg.adapter,
                    "quiesce",
                    reg.instance,
                    required=len(regs) > 1 or apply is not None,
                )
                quiesced.append(reg)
            snapshots = {
                reg.name: await _invoke(reg.adapter, "snapshot", reg.instance)
                for reg in regs
            }
            if apply is not None:
                definitions_applied = True
                applied = apply()
                if inspect.isawaitable(applied):
                    await applied
                entry.phases.append({"phase": "definitions-applied"})
                self._journal.persist(entry)
            for reg in regs:
                replacement = await _invoke(
                    reg.adapter, "prepare", changes, required=True
                )
                await _invoke(reg.adapter, "migrate", replacement, snapshots[reg.name])
                await _invoke(reg.adapter, "validate", replacement, required=True)
                prepared_components[reg.name] = replacement
                publication = getattr(reg.adapter, "publication", None)
                published = publication(replacement) if publication else replacement
                if inspect.isawaitable(published):
                    published = await published
                prepared_publications[reg.name] = published
            for name in plan.affected_bindings:
                binding = self.bindings[name]
                if binding.kind is BindingKind.PRESERVED:
                    continue
                if binding.kind is BindingKind.COMPONENT:
                    if (
                        not binding.component
                        or binding.component not in prepared_components
                    ):
                        raise ReconciliationRefused(
                            (
                                {
                                    "binding": name,
                                    "reason": "component binding has no affected adapter",
                                },
                            )
                        )
                    value = prepared_publications[binding.component]
                else:
                    if binding.prepare is None:
                        raise ReconciliationRefused(
                            (
                                {
                                    "binding": name,
                                    "reason": "binding does not provide safe preparation",
                                },
                            )
                        )
                    value = binding.prepare(changes)
                    value = await value if inspect.isawaitable(value) else value
                if binding.validate:
                    checked = binding.validate(value)
                    if inspect.isawaitable(checked):
                        await checked
                prepared_bindings[name] = value
            for name in plan.restarted:
                supervisor = self.task_supervisors[name]
                try:
                    prepared_tasks[name] = await supervisor.prepare_restart(
                        approved=approved
                    )
                except BaseException as task_error:
                    entry.phases.append(
                        {
                            "phase": "task-prepare-failed",
                            "task": name,
                            "policy": supervisor.policy.value,
                            "error": f"{type(task_error).__name__}: {task_error}",
                        }
                    )
                    self._journal.persist(entry)
                    raise
            entry.status = "prepared"
            entry.phases.append(
                {
                    "phase": "prepared",
                    "components": tuple(prepared_components),
                    "bindings": tuple(prepared_bindings),
                    "tasks": tuple(prepared_tasks),
                }
            )
            self._journal.persist(entry)
            replacements = {
                f"component:{name}": value
                for name, value in prepared_publications.items()
            }
            replacements.update(
                {f"binding:{name}": value for name, value in prepared_bindings.items()}
            )
            self.atomic_swap(replacements)
            swapped = True
            entry.status = "swapped"
            entry.recovery = "new-system-committed"
            entry.phases.append(
                {"phase": "atomic-swap", "generation": self._store.generation}
            )
            self._journal.persist(entry)
            for reg in regs:
                await _invoke(
                    reg.adapter, "commit", prepared_components[reg.name], required=True
                )
                reg.instance = prepared_publications[reg.name]
                await _invoke(reg.adapter, "resume", reg.instance)
            for name, prepared_task in prepared_tasks.items():
                supervisor = self.task_supervisors[name]
                await supervisor.commit_restart(prepared_task)
                committed_tasks.add(name)
                entry.phases.append(
                    {
                        "phase": "task-restarted",
                        "task": name,
                        "policy": supervisor.policy.value,
                    }
                )
            cleanup_errors = []
            for reg in reversed(regs):
                try:
                    await _invoke(
                        reg.adapter,
                        "stop",
                        old_snapshot[f"component:{reg.name}"],
                    )
                except BaseException as cleanup_error:  # noqa: BLE001 - record every retirement failure
                    cleanup_errors.append(
                        f"{reg.name}: {type(cleanup_error).__name__}: {cleanup_error}"
                    )
            if cleanup_errors:
                entry.status = "cleanup-incomplete"
                entry.recovery = "new-system-committed"
                entry.phases.append(
                    {"phase": "cleanup-incomplete", "errors": tuple(cleanup_errors)}
                )
            else:
                entry.status = "committed"
                entry.recovery = "new-system-committed"
                entry.phases.append({"phase": "cleanup-complete"})
            self._journal.persist(entry)
            return copy.deepcopy(asdict(entry))
        except BaseException as error:
            compensation_errors = []
            for name in reversed(tuple(prepared_tasks)):
                if name in committed_tasks:
                    compensation_errors.append(
                        f"{name}: committed task cannot restore its cancelled frame"
                    )
                    continue
                try:
                    await self.task_supervisors[name].abort_restart(
                        prepared_tasks[name]
                    )
                except BaseException as failure:  # noqa: BLE001 - abort every staged task
                    compensation_errors.append(f"{name}: {failure}")
            if swapped:
                self._store.restore(old_snapshot)
            if definitions_applied and rollback is not None:
                try:
                    reverted = rollback()
                    if inspect.isawaitable(reverted):
                        await reverted
                    entry.phases.append({"phase": "definitions-rolled-back"})
                except BaseException as failure:  # noqa: BLE001 - rollback is mandatory
                    compensation_errors.append(f"definitions: {failure}")
            restored = reversed(regs) if swapped else reversed(quiesced)
            for reg in restored:
                old = old_snapshot.get(f"component:{reg.name}")
                reg.instance = old
                try:
                    if swapped:
                        await _invoke(
                            reg.adapter, "compensate", error, required=True
                        )
                    await _invoke(reg.adapter, "resume", old)
                except BaseException as failure:  # noqa: BLE001 - cleanup must survive cancellation
                    compensation_errors.append(f"{reg.name}: {failure}")
            if not swapped:
                for reg in reversed(regs):
                    replacement = prepared_components.get(reg.name)
                    if replacement is not None:
                        try:
                            await _invoke(reg.adapter, "stop", replacement)
                        except BaseException as failure:  # noqa: BLE001 - dispose every prepared value
                            compensation_errors.append(f"{reg.name}: {failure}")
            entry.status = (
                "manual-recovery-required" if compensation_errors else "rolled-back"
            )
            entry.recovery = (
                "manual-recovery-required"
                if compensation_errors
                else "old-system-restored"
                if swapped
                else "old-system-active"
            )
            entry.phases.append(
                {
                    "phase": "compensation" if swapped else "pre-swap-failure",
                    "error": f"{type(error).__name__}: {error}",
                    "errors": tuple(compensation_errors),
                }
            )
            self._journal.persist(entry)
            raise


@dataclass
class PreparedTaskRestart:
    old: asyncio.Task | None
    replacement: asyncio.Task
    gate: asyncio.Event | None = None
    resume_drain: Callable[[], Any] | None = None


class TaskSupervisor:
    def __init__(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
        policy: TaskPolicy | str,
        *,
        watches: Iterable[str] = (),
        drain=None,
        readiness=None,
        timeout: float = 5.0,
    ):
        self.name = name
        self.factory = factory
        self.policy = TaskPolicy(policy)
        self.watches = tuple(watches)
        self.drain_hook = drain
        self.readiness = readiness
        self.timeout = timeout
        self.task = None
        self._safe_point = asyncio.Event()

    def start(self):
        if self.task is not None and not self.task.done():
            raise RuntimeError(f"Task {self.name!r} is already running")
        self._safe_point.clear()
        self.task = asyncio.create_task(self.factory(), name=self.name)
        return self.task

    def mark_safe_point(self):
        self._safe_point.set()

    async def _wait(self, awaitable, reason):
        try:
            return await asyncio.wait_for(awaitable, self.timeout)
        except asyncio.TimeoutError:
            raise ReconciliationRefused(
                ({"task": self.name, "reason": reason},)
            ) from None

    async def prepare_restart(self, *, approved=False) -> PreparedTaskRestart:
        if self.policy is TaskPolicy.NEVER:
            raise ReconciliationRefused(
                ({"task": self.name, "reason": "task policy is never"},)
            )
        if self.policy is TaskPolicy.MANUAL and not approved:
            raise ReconciliationRefused(
                (
                    {
                        "task": self.name,
                        "reason": "task policy requires operator approval",
                    },
                )
            )
        old = self.task
        resume_drain = None
        if self.policy is TaskPolicy.SAFE_POINT:
            await self._wait(self._safe_point.wait(), "safe point timeout")
        elif self.policy is TaskPolicy.DRAIN:
            if not self.drain_hook:
                raise ReconciliationRefused(
                    ({"task": self.name, "reason": "drain policy has no drain hook"},)
                )
            value = self.drain_hook()
            value = await value if inspect.isawaitable(value) else value
            resume_drain = value if callable(value) else None
        if self.policy is TaskPolicy.ROLLING:
            if self.readiness is None:
                raise ReconciliationRefused(
                    (
                        {
                            "task": self.name,
                            "reason": "rolling policy has no readiness check",
                        },
                    )
                )
            replacement = asyncio.create_task(self.factory(), name=self.name)
            readiness_task = None
            try:
                try:
                    accepts_replacement = bool(
                        inspect.signature(self.readiness).parameters
                    )
                except (TypeError, ValueError):
                    accepts_replacement = False
                value = (
                    self.readiness(replacement)
                    if accepts_replacement
                    else self.readiness()
                )
                readiness_task = asyncio.ensure_future(
                    value if inspect.isawaitable(value) else asyncio.sleep(0)
                )
                done, _ = await asyncio.wait(
                    {readiness_task, replacement},
                    timeout=self.timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if readiness_task not in done:
                    reason = (
                        "rolling replacement exited before readiness"
                        if replacement in done
                        else "rolling readiness timeout"
                    )
                    raise ReconciliationRefused(
                        ({"task": self.name, "reason": reason},)
                    )
                await readiness_task
                if replacement.done():
                    raise ReconciliationRefused(
                        (
                            {
                                "task": self.name,
                                "reason": (
                                    "rolling replacement exited before readiness"
                                ),
                            },
                        )
                    )
            except BaseException:
                if readiness_task is not None and not readiness_task.done():
                    readiness_task.cancel()
                    await asyncio.gather(readiness_task, return_exceptions=True)
                replacement.cancel()
                await asyncio.gather(replacement, return_exceptions=True)
                raise
            return PreparedTaskRestart(old, replacement, resume_drain=resume_drain)

        gate = asyncio.Event()

        async def staged():
            await gate.wait()
            return await self.factory()

        replacement = asyncio.create_task(staged(), name=self.name)
        return PreparedTaskRestart(old, replacement, gate, resume_drain)

    async def commit_restart(self, prepared: PreparedTaskRestart) -> None:
        if prepared.gate is not None:
            prepared.gate.set()
        if prepared.old and not prepared.old.done():
            prepared.old.cancel()
        if prepared.old:
            await asyncio.gather(prepared.old, return_exceptions=True)
        self.task = prepared.replacement
        self._safe_point.clear()
        if prepared.resume_drain is not None:
            value = prepared.resume_drain()
            if inspect.isawaitable(value):
                await value

    async def abort_restart(self, prepared: PreparedTaskRestart) -> None:
        if not prepared.replacement.done():
            prepared.replacement.cancel()
        await asyncio.gather(prepared.replacement, return_exceptions=True)
        if prepared.resume_drain is not None:
            value = prepared.resume_drain()
            if inspect.isawaitable(value):
                await value
        self.task = prepared.old

    async def restart(self, *, approved=False):
        prepared = await self.prepare_restart(approved=approved)
        await self.commit_restart(prepared)


def migrate_instances(instances, migrate, validate=None):
    selected = tuple(instances)
    snapshots = []
    for instance in selected:
        if not hasattr(instance, "__dict__"):
            raise ReconciliationRefused(
                (
                    {
                        "instance": repr(instance),
                        "reason": "instance has no restorable state dictionary",
                    },
                )
            )
        snapshots.append(copy.deepcopy(instance.__dict__))
    try:
        for instance in selected:
            migrate(instance)
            validate(instance) if validate else None
    except BaseException:
        for instance, snapshot in zip(selected, snapshots):
            instance.__dict__.clear()
            instance.__dict__.update(snapshot)
        raise


def _load_target(target: str) -> Any:
    module_name, separator, attribute = target.partition(":")
    loaded = importlib.import_module(module_name)
    value = getattr(loaded, attribute) if separator else loaded
    return value() if callable(value) and not hasattr(value, "name") else value


def discover_adapters(
    group: str = "dynra.adapters", *, project_targets: Iterable[str] = ()
) -> tuple[Any, ...]:
    eps = metadata.entry_points()
    selected = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, ())
    adapters = []
    for ep in sorted(selected, key=lambda item: item.name):
        loaded = ep.load()
        adapters.append(
            loaded() if callable(loaded) and not hasattr(loaded, "name") else loaded
        )
    adapters.extend(_load_target(target) for target in project_targets)
    return tuple(adapters)
