"""Dynra live Python runtime."""

from importlib.metadata import PackageNotFoundError, version

from .core import NamespaceManager, SourceConflictError
from .lifecycle import (
    BindingKind,
    ChangeSet,
    ComponentAdapter,
    ImpactPlan,
    LifecycleRuntime,
    ManagedHolder,
    ReconciliationRefused,
    RuntimeGraph,
    RuntimeView,
    TaskPolicy,
    TaskSupervisor,
    discover_adapters,
    migrate_instances,
)

__all__ = [
    "BindingKind",
    "ChangeSet",
    "ComponentAdapter",
    "ImpactPlan",
    "LifecycleRuntime",
    "ManagedHolder",
    "NamespaceManager",
    "ReconciliationRefused",
    "RuntimeGraph",
    "RuntimeView",
    "SourceConflictError",
    "TaskPolicy",
    "TaskSupervisor",
    "discover_adapters",
    "migrate_instances",
]

try:
    __version__ = version("dynra")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0+unknown"
