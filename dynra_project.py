"""Discover importable modules in a Python project without importing them."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


_IGNORED_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    path: Path
    is_package: bool


class ProjectLayout:
    """Resolved project roots and statically discovered Python modules."""

    def __init__(self, root: Path, source_roots: tuple[Path, ...]):
        self.root = root.resolve()
        self.source_roots = source_roots
        self.modules = self._discover_modules()

    @classmethod
    def discover(cls, root: str | Path) -> "ProjectLayout":
        project_root = Path(root).resolve()
        configured = cls._configured_roots(project_root)
        conventional = project_root / "src"
        if conventional.is_dir():
            configured.add(conventional.resolve())
        configured.add(project_root)
        roots = tuple(sorted(configured, key=lambda path: (path != project_root, str(path))))
        return cls(project_root, roots)

    @staticmethod
    def _configured_roots(root: Path) -> set[Path]:
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            return set()
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return set()

        roots: set[Path] = set()
        tool = data.get("tool", {})
        setuptools = tool.get("setuptools", {})
        package_dir = setuptools.get("package-dir", {})
        for value in package_dir.values():
            if value:
                roots.add((root / value).resolve())
        for value in setuptools.get("packages", {}).get("find", {}).get("where", []):
            roots.add((root / value).resolve())

        for package in tool.get("poetry", {}).get("packages", []):
            source = package.get("from")
            if source:
                roots.add((root / source).resolve())

        wheel_packages = (
            tool.get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("packages", [])
        )
        for package in wheel_packages:
            package_path = (root / package).resolve()
            roots.add(package_path.parent)

        return {path for path in roots if path.is_dir()}

    def activate(self) -> None:
        """Put specific source roots before the project root on ``sys.path``."""
        for root in self.source_roots:
            value = str(root)
            if value in sys.path:
                sys.path.remove(value)
            sys.path.insert(0, value)

    def _discover_modules(self) -> dict[str, ModuleInfo]:
        modules: dict[str, ModuleInfo] = {}
        nested_roots = set(self.source_roots) - {self.root}
        for source_root in self.source_roots:
            for path in source_root.rglob("*.py"):
                resolved = path.resolve()
                relative = resolved.relative_to(source_root)
                if any(part in _IGNORED_PARTS or part.startswith(".") for part in relative.parts):
                    continue
                if source_root == self.root and any(
                    resolved.is_relative_to(nested) for nested in nested_roots
                ):
                    continue
                parts = list(relative.with_suffix("").parts)
                is_package = parts[-1] == "__init__"
                if is_package:
                    parts.pop()
                if not parts or not all(part.isidentifier() for part in parts):
                    continue
                name = ".".join(parts)
                modules.setdefault(name, ModuleInfo(name, resolved, is_package))
        return modules

    def public_symbols(self, module_name: str) -> tuple[str, ...]:
        info = self.modules.get(module_name)
        if info is None:
            return ()
        try:
            tree = ast.parse(info.path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            return ()
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        return tuple(sorted(name for name in names if not name.startswith("_")))
    def module_for_path(self, filename: str | Path) -> str | None:
        try:
            path = Path(filename).resolve()
        except OSError:
            return None
        for name, info in self.modules.items():
            if info.path == path:
                return name
        return None


    def owns_file(self, filename: str | None) -> bool:
        if not filename or filename.startswith("<"):
            return False
        try:
            path = Path(filename).resolve()
        except OSError:
            return False
        return any(path == info.path for info in self.modules.values())
