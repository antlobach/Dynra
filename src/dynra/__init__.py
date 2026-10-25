"""Dynra live Python runtime."""

from importlib.metadata import PackageNotFoundError, version

from .core import NamespaceManager, SourceConflictError

__all__ = ["NamespaceManager", "SourceConflictError"]

try:
    __version__ = version("dynra")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0+unknown"
