"""Development-only Dynra adapter for live Stario applications."""

from __future__ import annotations

import asyncio
import copy
import sys
from dataclasses import dataclass
from typing import Any

from stario import App

from dynra.lifecycle import ChangeSet, ReconciliationRefused, RuntimeView

_ROUTE_FIELDS = (
    "_path",
    "_exact",
    "_hosts_exact",
    "_hosts_param",
    "_has_param_hosts",
    "_host_routing",
)
_ROUTE_REGISTRATIONS: dict[int, list[tuple[Any, Any, tuple[Any, ...]]]] = {}
_ORIGINAL_ADD = App.add


def _install_route_recorder() -> None:
    if getattr(App.add, "__dynra_route_recorder__", False):
        return

    def tracked_add(self, route, handler, *, middleware=()):
        _ROUTE_REGISTRATIONS.setdefault(id(self), []).append(
            (route, handler, tuple(middleware))
        )
        return _ORIGINAL_ADD(self, route, handler, middleware=middleware)

    tracked_add.__dynra_route_recorder__ = True
    App.add = tracked_add


_install_route_recorder()


def _looks_like_stario_app(value: Any) -> bool:
    return isinstance(value, App)


def _find_app() -> App:
    matches = []
    for module_name, module in sorted(sys.modules.items()):
        namespace = getattr(module, "__dict__", {})
        for name, value in namespace.items():
            if _looks_like_stario_app(value):
                matches.append((f"{module_name}.{name}", value))
    unique = {id(value): (name, value) for name, value in matches}
    if not unique:
        raise ReconciliationRefused(
            (
                {
                    "component": "stario-app",
                    "reason": "no loaded Stario application was discovered",
                    "suggested_action": "run the configured bootstrap first",
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
        "routes": {
            field: copy.copy(getattr(app, field, None)) for field in _ROUTE_FIELDS
        },
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

    async def discover(self, runtime: RuntimeView) -> object:
        self._app = _find_app()
        return self._app

    async def quiesce(self, app: App) -> None:
        if app.shutting_down:
            raise ReconciliationRefused(
                ({"component": self.name, "reason": "Stario app is shutting down"},)
            )
        # Yield one event-loop turn. Existing requests and SSE streams retain
        # their selected handlers; commit below replaces the router without await.
        await asyncio.sleep(0)

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
        self._snapshot = _state(app)
        return self._snapshot

    async def prepare(self, changes: ChangeSet) -> object:
        if self._app is None:
            raise ReconciliationRefused(
                ({"component": self.name, "reason": "adapter is not activated"},)
            )
        running_definitions = {
            definition
            for task in self._app.tasks
            if (definition := self._task_definition(task)) is not None
        }
        affected_tasks = tuple(sorted(set(changes.definitions) & running_definitions))
        if affected_tasks:
            raise ReconciliationRefused(
                (
                    {
                        "component": self.name,
                        "reason": "running Stario task frames cannot be replaced",
                        "tasks": affected_tasks,
                        "suggested_action": "register a Dynra TaskSupervisor policy or restart",
                    },
                )
            )
        registrations = _ROUTE_REGISTRATIONS.get(id(self._app))
        if not registrations:
            raise ReconciliationRefused(
                (
                    {
                        "component": self.name,
                        "reason": "route registrations predate dynra-stario tracking",
                        "suggested_action": "start Dynra before application bootstrap",
                    },
                )
            )
        candidate = App()
        for route, handler, middleware in registrations:
            _ORIGINAL_ADD(candidate, route, handler, middleware=middleware)
        return candidate

    async def migrate(self, replacement: App, snapshot: object) -> None:
        replacement._error_handlers = dict(snapshot["error_handlers"])

    async def validate(self, replacement: App) -> None:
        if replacement.shutting_down:
            raise ReconciliationRefused(
                (
                    {
                        "component": self.name,
                        "reason": "replacement router is shutting down",
                    },
                )
            )
        for field in _ROUTE_FIELDS:
            if not hasattr(replacement, field):
                raise ReconciliationRefused(
                    (
                        {
                            "component": self.name,
                            "reason": f"replacement router lacks {field}",
                        },
                    )
                )

    def publication(self, replacement: App) -> App:
        return self._app

    async def commit(self, replacement: App) -> None:
        for field in _ROUTE_FIELDS:
            setattr(self._app, field, getattr(replacement, field))
        self._app._error_handlers = dict(replacement._error_handlers)
        self._app._find_error_handler.cache_clear()

    async def resume(self, app: App) -> None:
        return None

    async def stop(self, app: App) -> None:
        # Candidate routers and the published app own no replacement listener.
        return None

    async def compensate(self, failure: BaseException) -> None:
        if self._snapshot is None or self._app is None:
            return
        for field, value in self._snapshot["routes"].items():
            setattr(self._app, field, value)
        self._app._error_handlers = dict(self._snapshot["error_handlers"])
        self._app._find_error_handler.cache_clear()


adapter = StarioAdapter()

__all__ = ["StarioAdapter", "adapter"]
