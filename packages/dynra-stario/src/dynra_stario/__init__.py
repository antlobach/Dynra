"""Development-only Dynra adapter for live Stario applications."""

from __future__ import annotations

import asyncio
import importlib
import sys
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from stario import App

from dynra.lifecycle import ChangeSet, ReconciliationRefused, RuntimeView


@dataclass(frozen=True)
class _CallableRef:
    module: str
    qualname: str
    fallback: Callable[..., Any]

    @classmethod
    def capture(cls, function: Callable[..., Any]) -> _CallableRef:
        return cls(
            getattr(function, "__module__", ""),
            getattr(function, "__qualname__", ""),
            function,
        )

    def resolve(self) -> Callable[..., Any]:
        if not self.module or "<locals>" in self.qualname:
            return self.fallback
        value: Any = importlib.import_module(self.module)
        try:
            for segment in self.qualname.split("."):
                value = getattr(value, segment)
        except AttributeError:
            return self.fallback
        return value if callable(value) else self.fallback


@dataclass
class _RequestControl:
    gate: asyncio.Event


_ROUTE_REGISTRATIONS: weakref.WeakKeyDictionary[
    App, list[tuple[Any, _CallableRef, tuple[_CallableRef, ...]]]
] = weakref.WeakKeyDictionary()
_ROUTE_COMPOSERS: weakref.WeakKeyDictionary[App, list[_CallableRef]] = (
    weakref.WeakKeyDictionary()
)
_OWNER_LOOPS: weakref.WeakKeyDictionary[App, asyncio.AbstractEventLoop] = (
    weakref.WeakKeyDictionary()
)
_REQUEST_CONTROLS: weakref.WeakKeyDictionary[App, _RequestControl] = (
    weakref.WeakKeyDictionary()
)
_PUBLISHED_ROUTERS: weakref.WeakKeyDictionary[App, App] = (
    weakref.WeakKeyDictionary()
)
_OBSERVED_APPS: weakref.WeakSet[App] = weakref.WeakSet()
_CANDIDATES: weakref.WeakSet[App] = weakref.WeakSet()
_ORIGINAL_ADD = App.add
_ORIGINAL_CALL = App.__call__
_ORIGINAL_FIND_HANDLER = App.find_handler
_ORIGINAL_HOST_ROUTING = App.host_routing.fget


def _control(app: App) -> _RequestControl:
    control = _REQUEST_CONTROLS.get(app)
    if control is None:
        gate = asyncio.Event()
        gate.set()
        control = _RequestControl(gate)
        _REQUEST_CONTROLS[app] = control
    return control


def _observe(app: App) -> None:
    if app not in _CANDIDATES:
        _OBSERVED_APPS.add(app)
    try:
        _OWNER_LOOPS[app] = asyncio.get_running_loop()
    except RuntimeError:
        pass
    _control(app)


def _install_route_recorder() -> None:
    if getattr(App.add, "__dynra_route_recorder__", False):
        return

    def tracked_add(self, route, handler, *, middleware=()):
        _observe(self)
        _ROUTE_REGISTRATIONS.setdefault(self, []).append(
            (
                route,
                _CallableRef.capture(handler),
                tuple(_CallableRef.capture(item) for item in middleware),
            )
        )
        return _ORIGINAL_ADD(self, route, handler, middleware=middleware)

    async def gated_call(self, context, writer):
        _observe(self)
        await _control(self).gate.wait()
        return await _ORIGINAL_CALL(self, context, writer)

    def routed_find_handler(self, host, path, method):
        router = _PUBLISHED_ROUTERS.get(self, self)
        return _ORIGINAL_FIND_HANDLER(router, host, path, method)

    def routed_host_routing(self):
        router = _PUBLISHED_ROUTERS.get(self, self)
        return _ORIGINAL_HOST_ROUTING(router)

    tracked_add.__dynra_route_recorder__ = True
    App.add = tracked_add
    App.__call__ = gated_call
    App.find_handler = routed_find_handler
    App.host_routing = property(routed_host_routing)


_install_route_recorder()


def compose_routes(app: App, composer: Callable[[App], Any]) -> None:
    """Register and run a side-effect-free, replayable route composition function."""
    _observe(app)
    _ROUTE_COMPOSERS.setdefault(app, []).append(_CallableRef.capture(composer))
    composer(app)


def _find_app() -> App:
    def eligible(app: App) -> bool:
        owner = _OWNER_LOOPS.get(app)
        return (
            app not in _CANDIDATES
            and owner is not None
            and not owner.is_closed()
        )

    matches = [
        (f"recorded:{id(app)}", app)
        for app in tuple(_OBSERVED_APPS)
        if eligible(app)
    ]
    for module_name, module in sorted(sys.modules.items()):
        namespace = getattr(module, "__dict__", {})
        for name, value in namespace.items():
            if isinstance(value, App) and eligible(value):
                matches.append((f"{module_name}.{name}", value))
    unique = {id(value): (name, value) for name, value in matches}
    if not unique:
        raise ReconciliationRefused(
            (
                {
                    "component": "stario-app",
                    "reason": "no loaded Stario application was discovered",
                    "suggested_action": "register routes after loading dynra-stario",
                },
            )
        )
    if len(unique) > 1:
        raise ReconciliationRefused(
            (
                {
                    "component": "stario-app",
                    "reason": "multiple loaded Stario applications are ambiguous",
                    "candidates": tuple(sorted(name for name, _ in unique.values())),
                },
            )
        )
    return next(iter(unique.values()))[1]


def _state(app: App) -> dict[str, Any]:
    return {
        "router": _PUBLISHED_ROUTERS.get(app, app),
        "error_handlers": dict(getattr(app, "_error_handlers", {})),
        "tasks": set(app.tasks),
        "shutdown": app.shutdown,
        "listener": getattr(app, "listener", getattr(app, "_listener", None)),
        "relay": getattr(app, "relay", getattr(app, "_relay", None)),
        "streams": set(getattr(app, "streams", getattr(app, "_streams", ()))),
    }


@dataclass
class StarioAdapter:
    name: str = "stario-app"
    dependencies: tuple[str, ...] = ()
    watches: tuple[str, ...] = ("*",)
    _app: App | None = None
    _snapshot: dict[str, Any] | None = None

    async def _on_owner(self, operation):
        if self._app is None:
            raise ReconciliationRefused(
                ({"component": self.name, "reason": "adapter is not activated"},)
            )
        owner = _OWNER_LOOPS.get(self._app)
        if owner is None or owner.is_closed():
            raise ReconciliationRefused(
                (
                    {
                        "component": self.name,
                        "reason": "Stario owner event loop is unavailable",
                    },
                )
            )
        if owner is asyncio.get_running_loop():
            return await operation()
        if not owner.is_running():
            raise ReconciliationRefused(
                (
                    {
                        "component": self.name,
                        "reason": "Stario owner event loop is not running",
                    },
                )
            )
        future = asyncio.run_coroutine_threadsafe(operation(), owner)
        return await asyncio.wrap_future(future)

    async def discover(self, runtime: RuntimeView) -> object:
        self._app = _find_app()
        if self._app not in _OWNER_LOOPS:
            raise ReconciliationRefused(
                (
                    {
                        "component": self.name,
                        "reason": "Stario application owner loop was not observed",
                        "suggested_action": (
                            "load dynra-stario before registering application routes"
                        ),
                    },
                )
            )
        return self._app

    async def quiesce(self, app: App) -> None:
        async def quiesce_on_owner():
            if app.shutting_down:
                raise ReconciliationRefused(
                    (
                        {
                            "component": self.name,
                            "reason": "Stario app is shutting down",
                        },
                    )
                )
            _control(app).gate.clear()
            # Requests admitted before the clear run synchronously through router
            # selection before yielding; later requests wait at the gate.
            await asyncio.sleep(0)

        await self._on_owner(quiesce_on_owner)

    @staticmethod
    def _task_definition(task: asyncio.Task) -> str | None:
        coroutine = task.get_coro()
        code = getattr(coroutine, "cr_code", None)
        frame = getattr(coroutine, "cr_frame", None)
        if code is None or frame is None:
            return None
        module_name = frame.f_globals.get("__name__")
        return f"{module_name}.{code.co_name}" if module_name else None

    async def snapshot(self, app: App) -> object:
        async def snapshot_on_owner():
            self._snapshot = _state(app)
            return self._snapshot

        return await self._on_owner(snapshot_on_owner)

    async def prepare(self, changes: ChangeSet) -> object:
        async def prepare_on_owner():
            assert self._app is not None
            running_definitions = {
                definition
                for task in self._app.tasks
                if (definition := self._task_definition(task)) is not None
            }
            affected_tasks = tuple(
                sorted(set(changes.definitions) & running_definitions)
            )
            if affected_tasks:
                raise ReconciliationRefused(
                    (
                        {
                            "component": self.name,
                            "reason": (
                                "running Stario task frames cannot be replaced"
                            ),
                            "tasks": affected_tasks,
                            "suggested_action": (
                                "register a Dynra TaskSupervisor policy or restart"
                            ),
                        },
                    )
                )
            composers = _ROUTE_COMPOSERS.get(self._app, ())
            registrations = _ROUTE_REGISTRATIONS.get(self._app, ())
            if not composers and not registrations:
                raise ReconciliationRefused(
                    (
                        {
                            "component": self.name,
                            "reason": (
                                "route registrations predate dynra-stario tracking"
                            ),
                            "suggested_action": (
                                "load dynra-stario before registering routes"
                            ),
                        },
                    )
                )
            candidate = App()
            _CANDIDATES.add(candidate)
            if composers:
                for composer in composers:
                    result = composer.resolve()(candidate)
                    if asyncio.iscoroutine(result):
                        await result
            else:
                for route, handler, middleware in registrations:
                    _ORIGINAL_ADD(
                        candidate,
                        route,
                        handler.resolve(),
                        middleware=tuple(item.resolve() for item in middleware),
                    )
            return candidate

        return await self._on_owner(prepare_on_owner)

    async def migrate(self, replacement: App, snapshot: object) -> None:
        async def migrate_on_owner():
            replacement._error_handlers = dict(snapshot["error_handlers"])
            replacement._find_error_handler.cache_clear()

        await self._on_owner(migrate_on_owner)

    async def validate(self, replacement: App) -> None:
        async def validate_on_owner():
            if replacement.shutting_down:
                raise ReconciliationRefused(
                    (
                        {
                            "component": self.name,
                            "reason": "replacement router is shutting down",
                        },
                    )
                )
            _ORIGINAL_FIND_HANDLER(replacement, "", "/", "GET")

        await self._on_owner(validate_on_owner)

    def publication(self, replacement: App) -> App:
        return self._app

    async def commit(self, replacement: App) -> None:
        async def commit_on_owner():
            assert self._app is not None
            _PUBLISHED_ROUTERS[self._app] = replacement

        await self._on_owner(commit_on_owner)

    async def resume(self, app: App) -> None:
        async def resume_on_owner():
            _control(app).gate.set()

        await self._on_owner(resume_on_owner)

    async def stop(self, app: App) -> None:
        # Candidate routers own no listener, relay, streams, or application tasks.
        return None

    async def compensate(self, failure: BaseException) -> None:
        if self._snapshot is None or self._app is None:
            raise RuntimeError("Stario compensation state is unavailable")

        async def compensate_on_owner():
            assert self._app is not None
            _PUBLISHED_ROUTERS[self._app] = self._snapshot["router"]

        await self._on_owner(compensate_on_owner)


adapter = StarioAdapter()

__all__ = ["StarioAdapter", "adapter", "compose_routes"]
