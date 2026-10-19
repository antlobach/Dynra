import ast
import builtins
import copy
import difflib
import gc
import importlib
import inspect
import json
import os
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from .project import ProjectLayout

#: CPython flag marking a code object as a coroutine.
CO_COROUTINE = 0x0080


def get_block_at_line(source, line_number):
    """Find the outermost AST node (def/class) containing the given line number."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    target_node = None
    for node in ast.walk(tree):
        # We look for top-level definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # lineno is 1-based
            if hasattr(node, "lineno") and hasattr(node, "end_lineno"):
                if node.lineno <= line_number <= node.end_lineno:
                    # If we haven't found a node yet, or this node is 'larger' (outer) than the current one
                    if target_node is None:
                        target_node = node
                    else:
                        # Prefer the outer definition when nested definitions overlap.
                        if (
                            node.lineno < target_node.lineno
                            or node.end_lineno > target_node.end_lineno
                        ):
                            target_node = node

    if target_node:
        lines = source.splitlines()
        return "\n".join(lines[target_node.lineno - 1 : target_node.end_lineno])
    return None


def analyze_function_patch_compatibility(old_func, new_func):
    """Return a list of reasons `old_func` cannot be patched in place.

    Mirrors `analyze_class_patch_compatibility`. The important case is the
    closure layout: CPython refuses `f.__code__ = c` when the code object needs
    a different number of free variables than the function's closure provides,
    and that refusal is the difference between a live patch and a half-applied
    one.
    """
    reasons = []

    if not isinstance(old_func, types.FunctionType) or not isinstance(
        new_func, types.FunctionType
    ):
        reasons.append("not both plain Python functions")
        return reasons

    old_free = old_func.__code__.co_freevars
    new_free = new_func.__code__.co_freevars
    if len(old_free) != len(new_free):
        reasons.append(f"closure layout changed: {old_free} -> {new_free}")

    old_coro = bool(old_func.__code__.co_flags & CO_COROUTINE)
    new_coro = bool(new_func.__code__.co_flags & CO_COROUTINE)
    if old_coro != new_coro:
        kind = "async -> sync" if old_coro else "sync -> async"
        reasons.append(f"function kind changed: {kind}")

    return reasons


def patch_function(old_func, new_func, safe_mode=True):
    """Update the code and metadata of an existing function object in place.

    Returns True when the patch was applied. In-place is the whole point: every
    holder of `old_func` - an importing module, a router, a callback list, a
    partial - keeps working and sees the new behaviour, which is what a Clojure
    var gives you for free.

    Annotations are only copied when the replacement actually declares them.
    Overwriting them unconditionally silently strips the types that frameworks
    doing signature-based dependency injection rely on.
    """
    if safe_mode:
        reasons = analyze_function_patch_compatibility(old_func, new_func)
        if reasons:
            print(
                f"⚠️ Skipping unsafe function patch for '{getattr(old_func, '__name__', old_func)}': "
                + "; ".join(reasons)
            )
            return False

    attrs = ["__code__", "__defaults__", "__doc__", "__kwdefaults__"]
    for attr in attrs:
        try:
            setattr(old_func, attr, getattr(new_func, attr))
        except (AttributeError, TypeError, ValueError) as error:
            print(
                f"⚠️ Could not patch {attr} on "
                f"'{getattr(old_func, '__name__', old_func)}': {error}"
            )
            return False

    new_annotations = getattr(new_func, "__annotations__", None)
    if new_annotations:
        old_func.__annotations__ = new_annotations

    return True


def _module_dict_ids():
    """Identity of every live module namespace, so referrer scans can skip them."""
    ids = set()
    for mod in list(sys.modules.values()):
        if isinstance(mod, types.ModuleType):
            try:
                ids.add(id(mod.__dict__))
            except Exception:
                pass
    return ids


def direct_holders(obj, ignore_ids=()):
    """Describe containers that hold `obj` directly, other than module namespaces.

    A function reachable only through module namespaces can be safely rebound,
    because name-based propagation will reach every copy. One captured into a
    router trie, a callback list, a `functools.partial` or a closure cell cannot:
    rebinding leaves that holder pointing at the old object forever. This is what
    makes a route handler patch appear to succeed and change nothing.
    """
    skip = set(ignore_ids) | _module_dict_ids()
    holders = []
    referrers = gc.get_referrers(obj)
    skip.add(id(referrers))
    for ref in referrers:
        if id(ref) in skip:
            continue
        if isinstance(ref, types.FrameType):
            continue
        if isinstance(ref, (list, tuple, set, frozenset)):
            holders.append(type(ref).__name__)
        elif isinstance(ref, dict):
            holders.append("dict")
        elif isinstance(ref, types.CellType):
            holders.append("closure cell")
        else:
            holders.append(type(ref).__name__)
    return holders


def analyze_class_patch_compatibility(old_cls, new_cls):
    """Return a list of unsafe class-shape changes for in-place patching."""
    reasons = []

    if type(old_cls) is not type(new_cls):
        reasons.append(
            f"metaclass changed: {type(old_cls).__name__} -> {type(new_cls).__name__}"
        )

    if old_cls.__bases__ != new_cls.__bases__:
        reasons.append(
            f"base classes changed: {old_cls.__bases__} -> {new_cls.__bases__}"
        )

    old_slots = getattr(old_cls, "__slots__", None)
    new_slots = getattr(new_cls, "__slots__", None)
    if old_slots != new_slots:
        reasons.append(f"__slots__ changed: {old_slots} -> {new_slots}")

    return reasons


def patch_class(old_cls, new_cls, safe_mode=True):
    """
    Update an existing class object with new methods/attributes (Common Lisp style).
    Existing instances will immediately see the new methods.
    """
    if safe_mode:
        reasons = analyze_class_patch_compatibility(old_cls, new_cls)
        if reasons:
            print(
                f"⚠️ Skipping unsafe class patch for '{old_cls.__name__}': "
                + "; ".join(reasons)
            )
            return False

    # 1. Update methods and class attributes
    for name, value in new_cls.__dict__.items():
        if name in ("__dict__", "__weakref__", "__module__", "__init__"):
            if name == "__init__":
                setattr(old_cls, name, value)
            continue
        try:
            setattr(old_cls, name, value)
        except (AttributeError, TypeError):
            pass

    # 2. Handle deletions
    old_names = set(old_cls.__dict__.keys())
    new_names = set(new_cls.__dict__.keys())
    for name in old_names - new_names:
        if name not in ("__dict__", "__weakref__", "__module__", "__doc__"):
            try:
                delattr(old_cls, name)
            except (AttributeError, TypeError):
                pass

    # 3. Common Lisp 'update-instance-for-redefined-class' Hook
    if hasattr(old_cls, "__dynra_update__"):
        for obj in gc.get_objects():
            if isinstance(obj, old_cls):
                try:
                    obj.__dynra_update__()
                except Exception as e:
                    print(f"Error updating instance {obj}: {e}")
    return True


@dataclass
class DefinitionSnapshot:
    module: str
    name: str
    exists: bool
    obj: object | None
    kind: str | None
    state: dict
    source: str | None
    sequence: int


@dataclass
class ManagedExecutionResult:
    success: bool
    result: object = None
    error: BaseException | None = None


class SourceConflictError(RuntimeError):
    """A source write would overwrite a definition changed outside Dynra."""


class NamespaceManager:
    def __init__(self, shell=None, project_root=None):
        self.shell = shell
        self.current_name = "user"
        self.safe_class_patching = True
        self.safe_function_patching = True
        self.prev_ns_snapshot = {}
        self.prev_objects = {}
        self.project = ProjectLayout.discover(project_root or os.getcwd())
        self.project.activate()
        self.synthetic_modules: set[str] = set()
        self.history: list[dict] = []
        self._undo_stacks: dict[tuple[str, str], list[DefinitionSnapshot]] = {}
        self._live_sources: dict[tuple[str, str], str | None] = {}
        self._source_bases: dict[tuple[str, str], str | None] = {}
        self._source_snapshots: dict[str, dict[str, str]] = {}
        self._source_signatures: dict[str, tuple[int, int, int]] = {}
        self._source_conflicts: dict[str, tuple[str, ...]] = {}
        self._module_generations: dict[str, int] = {}
        self.last_source_sync: dict = {}
        self._reconciling_source = False
        self._executing_module_name: str | None = None
        self._sequence = 0
        self._active_snapshot: DefinitionSnapshot | None = None
        self._pending_source: str | None = None
        self._patch_refused = False
        self.create_md("user")
        self.install_commands()

    @property
    def commands(self):
        return {
            "create_md": self.create_md,
            "require_md": self.require_md,
            "in_md": self.in_md,
            "ls_md": self.ls_md,
            "help_dynra": self.help_dynra,
            "dir_md": self.dir_md,
            "doc": self.doc,
            "source": self.source,
            "find_var": self.find_var,
            "class_patch_mode": self.class_patch_mode,
            "function_patch_mode": self.function_patch_mode,
            "unmap": self.unmap,
            "patch_log": self.patch_log,
            "revert": self.revert,
            "revert_all": self.revert_all,
            "diverged": self.diverged,
            "source_patch": self.source_patch,
            "persist_source": self.persist_source,
            "source_sync_status": self.source_sync_status,
        }

    def install_commands(self):
        """Expose REPL commands through builtins, not application module globals."""
        for name, command in self.commands.items():
            setattr(builtins, name, command)

    @staticmethod
    def _validate_module_name(name):
        if not isinstance(name, str) or not name or not all(
            part.isidentifier() for part in name.split(".")
        ):
            raise ValueError(f"Invalid Python module name: {name!r}")

    def _create_module(self, name):
        self._validate_module_name(name)
        if name in self.project.modules:
            raise ValueError(
                f"{name!r} is a project module; use require_md({name!r})"
            )
        existing = sys.modules.get(name)
        if existing is not None and name not in self.synthetic_modules:
            if getattr(existing, "__file__", None) != f"<repl:{name}>":
                raise ValueError(f"{name!r} is already a real loaded module")
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__file__ = f"<repl:{name}>"
            mod.__package__ = name.rpartition(".")[0]
            mod.__loader__ = None
            mod.__spec__ = None
            sys.modules[name] = mod
        self.synthetic_modules.add(name)
        return sys.modules[name]

    def create_md(self, name):
        """Create an explicit synthetic REPL module without entering it."""
        return self._create_module(name)

    def require_md(self, name):
        """Import an existing module, preserving the original import failure."""
        self._validate_module_name(name)
        if name in self.synthetic_modules:
            return sys.modules[name]
        return importlib.import_module(name)

    def _init_ipython_ns(self, mod_dict):
        if not self.shell:
            return
        sh = self.shell
        infra = {
            "_ih": sh.user_ns.get("_ih", []),
            "_oh": sh.user_ns.get("_oh", {}),
            "_dh": sh.user_ns.get("_dh", []),
            "In": sh.user_ns.get("In", []),
            "Out": sh.user_ns.get("Out", {}),
            "get_ipython": sh.user_ns.get("get_ipython"),
            "exit": sh.user_ns.get("exit"),
            "quit": sh.user_ns.get("quit"),
            "open": sh.user_ns.get("open"),
        }
        for key, value in infra.items():
            if key not in mod_dict or key in {"In", "Out", "_oh", "_ih"}:
                mod_dict[key] = value

    def in_md(self, name, *, announce=True):
        """Enter a loaded, discoverable, or importable module."""
        already_tracked = name in self._source_snapshots
        mod = self.require_md(name)
        self.current_name = name
        if self.shell:
            self._init_ipython_ns(mod.__dict__)
            self.shell.user_ns = mod.__dict__
            self.shell.user_module = mod
            if announce:
                print(f"Switched to module '{name}'")
        if already_tracked:
            self.last_source_sync = self.reconcile_module_source(name)
            if self.shell:
                self._init_ipython_ns(mod.__dict__)
                self.shell.user_ns = mod.__dict__
                self.shell.user_module = mod
        else:
            self._remember_module_source(name)
            self.last_source_sync = self.source_sync_status(name)
        return mod

    def module_generation(self, name=None):
        return self._module_generations.get(name or self.current_name, 0)

    def _bump_module_generation(self, name):
        generation = self._module_generations.get(name, 0) + 1
        self._module_generations[name] = generation
        return generation

    @staticmethod
    def _file_signature(path):
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0)

    def _read_module_definitions(self, name):
        info = self.project.modules.get(name)
        if info is None:
            return None, {}
        text = info.path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        definitions = {
            node.name: self._statement_source(text, node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        return info.path, definitions

    def _remember_module_source(self, name):
        path, definitions = self._read_module_definitions(name)
        if path is None:
            return
        self._source_snapshots[name] = definitions
        self._source_signatures[name] = self._file_signature(path)
        self._source_conflicts.pop(name, None)

    def source_sync_status(self, module=None):
        name = module or self.current_name
        conflicts = self._source_conflicts.get(name, ())
        live = tuple(
            sorted(
                f"{mod}.{definition}"
                for mod, definition in self._live_sources
                if mod == name
            )
        )
        status = "conflict" if conflicts else "live-newer" if live else "clean"
        return {
            "module": name,
            "generation": self.module_generation(name),
            "status": status,
            "conflicts": conflicts,
            "live": live,
            "changes": (),
        }

    def _remove_module_bindings(self, origin, obj):
        removed = []
        for module_name, module in list(sys.modules.items()):
            if not isinstance(module, types.ModuleType):
                continue
            for attr, value in list(module.__dict__.items()):
                if value is obj and getattr(value, "__module__", None) == origin:
                    del module.__dict__[attr]
                    removed.append(f"{module_name}.{attr}")
        return tuple(sorted(removed))

    def reconcile_module_source(self, name=None):
        """Apply external top-level definition edits to a loaded module."""
        module_name = name or self.current_name
        info = self.project.modules.get(module_name)
        if info is None or module_name not in sys.modules:
            return self.source_sync_status(module_name)
        try:
            signature = self._file_signature(info.path)
            if (
                self._source_signatures.get(module_name) == signature
                and module_name not in self._source_conflicts
            ):
                return self.source_sync_status(module_name)
            _, disk = self._read_module_definitions(module_name)
        except (OSError, SyntaxError, UnicodeError) as error:
            report = self.source_sync_status(module_name)
            report.update(status="error", error=f"{type(error).__name__}: {error}")
            return report

        base = self._source_snapshots.get(module_name, {})
        changed_names = {
            definition
            for definition in set(base) | set(disk)
            if not self._same_definition(base.get(definition), disk.get(definition))
        }
        changes = []
        conflicts = []
        self._reconciling_source = True
        try:
            for definition in sorted(changed_names):
                key = (module_name, definition)
                disk_source = disk.get(definition)
                live_source = self._live_sources.get(key, base.get(definition))
                if key in self._live_sources and not self._same_definition(
                    live_source, disk_source
                ):
                    conflicts.append(f"{module_name}.{definition}")
                    continue
                if disk_source is None:
                    obj = sys.modules[module_name].__dict__.get(definition)
                    removed = (
                        self._remove_module_bindings(module_name, obj)
                        if isinstance(obj, (types.FunctionType, type))
                        else ()
                    )
                    changes.append(
                        {
                            "definition": f"{module_name}.{definition}",
                            "action": "removed",
                            "bindings": removed,
                        }
                    )
                    continue
                if not self.shell:
                    conflicts.append(f"{module_name}.{definition}")
                    continue
                result = self.execute_code(disk_source)
                if not result.success:
                    conflicts.append(f"{module_name}.{definition}")
                    continue
                changes.append(
                    {
                        "definition": f"{module_name}.{definition}",
                        "action": "added"
                        if definition not in base
                        else "updated",
                    }
                )
        finally:
            self._reconciling_source = False

        if changes:
            self._bump_module_generation(module_name)
            hook = sys.modules[module_name].__dict__.get("__dynra_reconcile__")
            if callable(hook):
                try:
                    hook(tuple(dict(change) for change in changes))
                except Exception as error:
                    changes.append(
                        {
                            "definition": f"{module_name}.__dynra_reconcile__",
                            "action": "hook-error",
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )

        snapshot = dict(base)
        for definition in changed_names:
            if f"{module_name}.{definition}" in conflicts:
                continue
            if definition in disk:
                snapshot[definition] = disk[definition]
            else:
                snapshot.pop(definition, None)
            key = (module_name, definition)
            self._live_sources.pop(key, None)
            self._source_bases.pop(key, None)
            self._undo_stacks.pop(key, None)
        self._source_snapshots[module_name] = snapshot
        self._source_signatures[module_name] = signature
        if conflicts:
            self._source_conflicts[module_name] = tuple(sorted(conflicts))
        else:
            self._source_conflicts.pop(module_name, None)
        status = "conflict" if conflicts else "healed" if changes else "clean"
        return {
            "module": module_name,
            "generation": self.module_generation(module_name),
            "status": status,
            "conflicts": tuple(sorted(conflicts)),
            "live": self.source_sync_status(module_name)["live"],
            "changes": tuple(changes),
        }

    def reconcile_tracked_sources(self):
        """Heal every tracked project module before evaluating the current one."""
        if not self.shell:
            return self.reconcile_module_source()
        original_module = self.shell.user_module
        original_namespace = self.shell.user_ns
        original_name = self.current_name
        reports = []
        try:
            for module_name in sorted(self._source_snapshots):
                module = sys.modules.get(module_name)
                if not isinstance(module, types.ModuleType):
                    continue
                self._init_ipython_ns(module.__dict__)
                self.shell.user_ns = module.__dict__
                self.shell.user_module = module
                self.current_name = module_name
                report = self.reconcile_module_source(module_name)
                if (
                    report["status"] != "clean"
                    or report["changes"]
                    or report["conflicts"]
                ):
                    reports.append(report)
        finally:
            self._init_ipython_ns(original_namespace)
            self.shell.user_ns = original_namespace
            self.shell.user_module = original_module
            self.current_name = original_name

        aggregate = self.source_sync_status(original_name)
        if not reports:
            return aggregate
        aggregate["changes"] = tuple(
            change for report in reports for change in report["changes"]
        )
        aggregate["conflicts"] = tuple(
            conflict for report in reports for conflict in report["conflicts"]
        )
        errors = tuple(
            report["error"] for report in reports if report.get("error")
        )
        if errors:
            aggregate["status"] = "error"
            aggregate["errors"] = errors
        elif aggregate["conflicts"]:
            aggregate["status"] = "conflict"
        elif aggregate["changes"]:
            aggregate["status"] = "healed"
        return aggregate

    def module_catalog(self):
        discovered = set(self.project.modules)
        loaded_project = {
            name
            for name, module in sys.modules.items()
            if isinstance(module, types.ModuleType)
            and (
                name in discovered
                or self.project.owns_file(getattr(module, "__file__", None))
            )
        }
        synthetic = set(self.synthetic_modules)
        external = {
            name
            for name, module in sys.modules.items()
            if isinstance(module, types.ModuleType)
            and name not in loaded_project
            and name not in synthetic
        }
        return {
            "discovered": tuple(sorted(discovered)),
            "loaded_project": tuple(sorted(loaded_project)),
            "synthetic": tuple(sorted(synthetic)),
            "external": tuple(sorted(external)),
        }

    def ls_md(self):
        catalog = self.module_catalog()
        print("Discovered project modules:", ", ".join(catalog["discovered"]) or "(none)")
        print("Loaded project modules:", ", ".join(catalog["loaded_project"]) or "(none)")
        print("Synthetic REPL modules:", ", ".join(catalog["synthetic"]) or "(none)")
        print(f"External loaded modules: {len(catalog['external'])}")
        return catalog

    def doc(self, obj):
        print(inspect.getdoc(obj) or "No documentation found.")

    def source(self, obj):
        try:
            print(inspect.getsource(obj))
        except Exception as error:
            print(f"Could not get source: {error}")

    def dir_md(self, module=None):
        """List public symbols without importing a discoverable unloaded module."""
        if module is None:
            target = self.shell.user_ns if self.shell else {}
            publics = [name for name in target if not name.startswith("_")]
        elif isinstance(module, str):
            if module in sys.modules:
                publics = [
                    name for name in sys.modules[module].__dict__ if not name.startswith("_")
                ]
            else:
                publics = list(self.project.public_symbols(module))
        else:
            publics = [name for name in module.__dict__ if not name.startswith("_")]
        result = tuple(sorted(publics))
        print("\n".join(result))
        return result

    def find_var(self, pattern):
        matcher = re.compile(pattern)
        results = []
        for mod_name, mod in sys.modules.items():
            if not isinstance(mod, types.ModuleType):
                continue
            for attr in mod.__dict__:
                if matcher.search(attr):
                    results.append(f"{mod_name}/{attr}")
        result = tuple(sorted(results))
        print("\n".join(result))
        return result

    def class_patch_mode(self, mode=None):
        return self._patch_mode("class", mode)

    def function_patch_mode(self, mode=None):
        return self._patch_mode("function", mode)

    def _patch_mode(self, kind, mode):
        attr = f"safe_{kind}_patching"
        if mode is not None:
            normalized = str(mode).strip().lower()
            if normalized in {"safe", "on", "true", "1"}:
                setattr(self, attr, True)
            elif normalized in {"permissive", "off", "false", "0"}:
                setattr(self, attr, False)
            else:
                raise ValueError("mode must be 'safe' or 'permissive'")
        current = "safe" if getattr(self, attr) else "permissive"
        print(f"{kind}_patch_mode: {current}")
        return current

    def help_dynra(self):
        print(
            """dynra commands:
  create_md(name)          - Create a synthetic REPL module
  require_md(name)         - Import an existing module, surfacing import errors
  in_md(name)              - Enter an existing real or synthetic module
  ls_md()                  - List discovered, loaded, synthetic, and external modules
  dir_md([module])         - List public symbols; statically inspects unloaded modules
  doc(obj) / source(obj)   - Inspect documentation and source
  find_var(pattern)        - Search loaded modules for symbols
  function_patch_mode()    - Get/set safe|permissive function patching
  class_patch_mode()       - Get/set safe|permissive class patching
  unmap(name)              - Remove a definition and report remaining holders
  patch_log()              - Show applied, refused, reverted, and persisted changes
  revert(target)           - Undo the latest change to a definition
  revert_all()             - Undo all live changes
  diverged()               - Show live definitions that differ from disk
  source_patch()           - Emit unified diffs for live changes
  persist_source([target]) - Write exact live definitions back to source
  source_sync_status()     - Show source freshness and module generation
"""
        )

    def pre_execute(self):
        if not self.shell:
            return
        if not self._reconciling_source:
            self.last_source_sync = self.reconcile_tracked_sources()
        self._executing_module_name = getattr(
            self.shell.user_module, "__name__", self.current_name
        )
        namespace = self.shell.user_ns
        self.prev_ns_snapshot = {name: id(value) for name, value in namespace.items()}
        self.prev_objects = dict(namespace)
        self._patch_refused = False

    def post_execute(self):
        if not self.shell:
            return
        namespace = self.shell.user_ns
        module_name = getattr(self.shell.user_module, "__name__", None)
        if not module_name:
            return
        if module_name != self._executing_module_name:
            self.prev_ns_snapshot = {
                name: id(value) for name, value in namespace.items()
            }
            self.prev_objects = dict(namespace)
            return
        for name, obj in list(namespace.items()):
            if name.startswith("_") or getattr(obj, "__module__", None) != module_name:
                continue
            if not isinstance(obj, (types.FunctionType, type)):
                continue
            previous = self.prev_objects.get(name)
            if previous is obj:
                continue
            if name not in self.prev_ns_snapshot:
                before = self._capture_snapshot(module_name, name, exists=False)
                self._record_change("define", before, self._pending_source)
                continue
            before = self._capture_object_snapshot(module_name, name, previous)
            self._active_snapshot = before
            try:
                if not self._redefine_in_place(name, previous, obj, namespace):
                    self._patch_refused = True
                    self._record_event("refused", module_name, name, "incompatible redefinition")
                    continue
            finally:
                self._active_snapshot = None
            canonical = namespace.get(name, obj)
            self._replace_mutable_references(obj, canonical, namespace)
            self._propagate_update(name, canonical, module_name)
            self._record_change("patch", before, self._pending_source)

    def _capture_object_snapshot(self, module, name, obj):
        if isinstance(obj, types.FunctionType):
            state = {
                "__code__": obj.__code__,
                "__defaults__": obj.__defaults__,
                "__doc__": obj.__doc__,
                "__kwdefaults__": obj.__kwdefaults__,
                "__annotations__": dict(getattr(obj, "__annotations__", {})),
                "__dict__": dict(obj.__dict__),
            }
            kind = "function"
        elif isinstance(obj, type):
            state = dict(obj.__dict__)
            kind = "class"
        else:
            state = {}
            kind = None
        key = (module, name)
        previous_source = (
            self._live_sources[key]
            if key in self._live_sources
            else self._disk_definition(module, name)[0]
        )
        return DefinitionSnapshot(
            module,
            name,
            True,
            obj,
            kind,
            state,
            previous_source,
            self._sequence,
        )

    def _capture_snapshot(self, module, name, exists=None):
        namespace = sys.modules[module].__dict__
        if exists is False or name not in namespace:
            return DefinitionSnapshot(
                module,
                name,
                False,
                None,
                None,
                {},
                self._live_sources.get((module, name)),
                self._sequence,
            )
        return self._capture_object_snapshot(module, name, namespace[name])

    def _restore_snapshot(self, snapshot):
        namespace = sys.modules[snapshot.module].__dict__
        key = (snapshot.module, snapshot.name)
        if not snapshot.exists:
            namespace.pop(snapshot.name, None)
        elif snapshot.kind == "function":
            fn = snapshot.obj
            for attr in (
                "__code__",
                "__defaults__",
                "__doc__",
                "__kwdefaults__",
                "__annotations__",
            ):
                setattr(fn, attr, snapshot.state[attr])
            fn.__dict__.clear()
            fn.__dict__.update(snapshot.state["__dict__"])
            namespace[snapshot.name] = fn
        elif snapshot.kind == "class":
            cls = snapshot.obj
            protected = {"__dict__", "__weakref__", "__module__", "__classcell__"}
            for name in set(cls.__dict__) - set(snapshot.state):
                if name not in protected:
                    try:
                        delattr(cls, name)
                    except (AttributeError, TypeError):
                        pass
            for name, value in snapshot.state.items():
                if name not in protected:
                    try:
                        setattr(cls, name, value)
                    except (AttributeError, TypeError):
                        pass
            namespace[snapshot.name] = cls
        else:
            namespace[snapshot.name] = snapshot.obj
        if snapshot.source is None:
            self._live_sources.pop(key, None)
        else:
            self._live_sources[key] = snapshot.source

    def _clear_synced_live_source(self, module, name):
        key = (module, name)
        if key not in self._live_sources:
            return
        disk_source = self._disk_definition(module, name)[0]
        if self._same_definition(self._live_sources[key], disk_source):
            self._live_sources.pop(key, None)
            self._source_bases.pop(key, None)

    def _record_change(self, action, before, source):
        if self._reconciling_source:
            return
        self._sequence += 1
        before.sequence = self._sequence
        key = (before.module, before.name)
        if key not in self._source_bases:
            self._source_bases[key] = self._disk_definition(
                before.module, before.name
            )[0]
        self._undo_stacks.setdefault(key, []).append(before)
        self._live_sources[key] = source
        self._bump_module_generation(before.module)
        self.history.append(
            {
                "sequence": self._sequence,
                "action": action,
                "status": "applied",
                "module": before.module,
                "name": before.name,
            }
        )

    def _record_event(self, action, module, name, detail=""):
        self._sequence += 1
        self.history.append(
            {
                "sequence": self._sequence,
                "action": action,
                "status": "recorded",
                "module": module,
                "name": name,
                "detail": detail,
            }
        )

    def _internal_holder_ids(self):
        ids = {id(self.prev_objects), id(self.history), id(self._undo_stacks)}
        if self._active_snapshot is not None:
            ids.add(id(self._active_snapshot))
        for stack in self._undo_stacks.values():
            ids.add(id(stack))
            ids.update(id(snapshot) for snapshot in stack)
        return ids

    def _redefine_in_place(self, name, old_obj, new_obj, current_ns):
        is_fn = isinstance(old_obj, types.FunctionType) and isinstance(
            new_obj, types.FunctionType
        )
        is_cls = isinstance(old_obj, type) and isinstance(new_obj, type)
        if not (is_fn or is_cls):
            return True
        reasons = (
            analyze_function_patch_compatibility(old_obj, new_obj)
            if is_fn
            else analyze_class_patch_compatibility(old_obj, new_obj)
        )
        if not reasons:
            ok = (
                patch_function(old_obj, new_obj, safe_mode=False)
                if is_fn
                else patch_class(old_obj, new_obj, safe_mode=False)
            )
            if ok:
                current_ns[name] = old_obj
            return ok

        holders = direct_holders(
            old_obj,
            ignore_ids={id(current_ns), id(self.prev_objects)}
            | self._internal_holder_ids(),
        )
        safe = self.safe_function_patching if is_fn else self.safe_class_patching
        if holders and safe:
            current_ns[name] = old_obj
            print(
                f"⚠️ Refusing to redefine '{name}': {'; '.join(reasons)}. "
                f"It is held directly by {sorted(set(holders))}, which would keep "
                "running the old code."
            )
            return False
        if holders:
            print(
                f"⚠️ Rebound '{name}' but {sorted(set(holders))} still reference "
                f"the previous object: {'; '.join(reasons)}"
            )
        return True

    def _replace_mutable_references(self, discarded, canonical, current_ns):
        if discarded is canonical:
            return
        referrers = gc.get_referrers(discarded)
        ignored = {
            id(referrers),
            id(current_ns),
            id(self.prev_objects),
        } | self._internal_holder_ids() | _module_dict_ids()
        for holder in referrers:
            if id(holder) in ignored or isinstance(holder, types.FrameType):
                continue
            if isinstance(holder, dict):
                for key, value in list(holder.items()):
                    if value is discarded:
                        holder[key] = canonical
            elif isinstance(holder, list):
                for index, value in enumerate(holder):
                    if value is discarded:
                        holder[index] = canonical
            elif isinstance(holder, set) and discarded in holder:
                holder.remove(discarded)
                holder.add(canonical)

    def _propagate_update(self, name, new_obj, origin_mod_name):
        for mod_name, mod in list(sys.modules.items()):
            if mod_name == origin_mod_name or not isinstance(mod, types.ModuleType):
                continue
            old_obj = mod.__dict__.get(name)
            if getattr(old_obj, "__module__", None) != origin_mod_name:
                continue
            if isinstance(new_obj, types.FunctionType) and isinstance(
                old_obj, types.FunctionType
            ):
                patch_function(old_obj, new_obj)
            elif isinstance(new_obj, type) and isinstance(old_obj, type):
                patch_class(old_obj, new_obj, safe_mode=self.safe_class_patching)

    def execute_code(self, code, *, silent=False):
        """Evaluate top-level statements sequentially and roll back failed batches."""
        if not self.shell:
            raise RuntimeError("execute_code requires an attached shell")
        try:
            tree = ast.parse(code)
        except SyntaxError:
            result = self.shell.run_cell(code, store_history=False, silent=silent)
            return ManagedExecutionResult(result.success, result.result)

        module_name = getattr(self.shell.user_module, "__name__", self.current_name)
        self.current_name = module_name
        namespace = self.shell.user_ns
        definition_names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        transaction = {
            name: self._capture_snapshot(module_name, name)
            for name in definition_names
        }
        history_length = len(self.history)
        undo_lengths = {key: len(stack) for key, stack in self._undo_stacks.items()}
        live_sources = dict(self._live_sources)
        source_bases = dict(self._source_bases)
        module_generations = dict(self._module_generations)
        last_result = None
        result_name = f"__dynra_result_{id(self):x}"

        for node in tree.body:
            source = self._statement_source(code, node)
            executable = source
            captures_result = silent and isinstance(node, ast.Expr)
            if captures_result:
                assignment = ast.Assign(
                    targets=[ast.Name(id=result_name, ctx=ast.Store())],
                    value=node.value,
                )
                executable = ast.unparse(ast.fix_missing_locations(assignment))
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.decorator_list
                and node.name in namespace
            ):
                undecorated = copy.deepcopy(node)
                undecorated.decorator_list = []
                ast.fix_missing_locations(undecorated)
                executable = ast.unparse(undecorated)
                print(
                    f"↻ Redefining '{node.name}' without re-running its decorators."
                )
            self._pending_source = source
            try:
                result = self.shell.run_cell(
                    executable, store_history=False, silent=silent
                )
            finally:
                self._pending_source = None
            if not result.success or self._patch_refused:
                namespace.pop(result_name, None)
                for snapshot in transaction.values():
                    self._restore_snapshot(snapshot)
                del self.history[history_length:]
                for key in list(self._undo_stacks):
                    keep = undo_lengths.get(key, 0)
                    del self._undo_stacks[key][keep:]
                    if not self._undo_stacks[key]:
                        del self._undo_stacks[key]
                self._live_sources = live_sources
                self._source_bases = source_bases
                self._module_generations = module_generations
                self._record_event(
                    "transaction",
                    module_name,
                    ",".join(sorted(definition_names)),
                    "rolled back",
                )
                return ManagedExecutionResult(
                    False,
                    result.result,
                    getattr(result, "error_in_exec", None)
                    or getattr(result, "error_before_exec", None),
                )
            last_result = (
                namespace.pop(result_name, None)
                if captures_result
                else result.result
            )
        return ManagedExecutionResult(True, last_result)

    @staticmethod
    def _statement_source(code, node):
        if getattr(node, "decorator_list", None):
            lines = code.splitlines()
            start = node.decorator_list[0].lineno - 1
            return "\n".join(lines[start : node.end_lineno])
        return ast.get_source_segment(code, node) or ast.unparse(node)

    def _resolve_target(self, target):
        if isinstance(target, (types.FunctionType, type)):
            return target.__module__, target.__name__
        if not isinstance(target, str):
            raise TypeError("target must be a definition or qualified name")
        if self.current_name in sys.modules and target in sys.modules[
            self.current_name
        ].__dict__:
            return self.current_name, target
        candidates = sorted(
            (
                name
                for name in set(sys.modules) | set(self.project.modules)
                if target.startswith(name + ".")
            ),
            key=len,
            reverse=True,
        )
        if not candidates:
            raise KeyError(f"Cannot resolve definition {target!r}")
        module = candidates[0]
        return module, target[len(module) + 1 :]

    def unmap(self, name, module=None):
        module_name = module or self.current_name
        namespace = sys.modules[module_name].__dict__
        if name not in namespace:
            raise KeyError(f"{module_name}.{name} is not mapped")
        obj = namespace[name]
        before = self._capture_object_snapshot(module_name, name, obj)
        del namespace[name]
        self._record_change("unmap", before, None)
        holders = direct_holders(
            obj,
            ignore_ids=self._internal_holder_ids() | {id(namespace)},
        )
        result = tuple(sorted(set(holders)))
        print(
            f"Unmapped {module_name}.{name}; remaining holders: "
            + (", ".join(result) if result else "(none)")
        )
        return result

    def patch_log(self):
        snapshot = tuple(dict(entry) for entry in self.history)
        for entry in snapshot:
            print(json.dumps(entry, sort_keys=True))
        return snapshot

    def revert(self, target):
        module, name = self._resolve_target(target)
        key = (module, name)
        stack = self._undo_stacks.get(key)
        if not stack:
            raise KeyError(f"No live change to revert for {module}.{name}")
        snapshot = stack.pop()
        self._restore_snapshot(snapshot)
        self._clear_synced_live_source(module, name)
        if not stack:
            self._undo_stacks.pop(key, None)
        self._bump_module_generation(module)
        self._record_event("revert", module, name)
        return sys.modules[module].__dict__.get(name)

    def revert_all(self):
        count = 0
        while self._undo_stacks:
            key, stack = max(
                self._undo_stacks.items(), key=lambda item: item[1][-1].sequence
            )
            snapshot = stack.pop()
            self._restore_snapshot(snapshot)
            self._clear_synced_live_source(snapshot.module, snapshot.name)
            if not stack:
                del self._undo_stacks[key]
            self._bump_module_generation(snapshot.module)
            self._record_event("revert", snapshot.module, snapshot.name)
            count += 1
        print(f"Reverted {count} live change(s).")
        return count

    def _disk_definition(self, module, name):
        info = self.project.modules.get(module)
        if info is None:
            return None, None, None
        text = info.path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == name:
                    start = (
                        node.decorator_list[0].lineno
                        if node.decorator_list
                        else node.lineno
                    )
                    return (
                        self._statement_source(text, node),
                        info.path,
                        (start, node.end_lineno),
                    )
        return None, info.path, None

    @staticmethod
    def _same_definition(left, right):
        if left is None or right is None:
            return left is right
        try:
            return ast.dump(ast.parse(left), include_attributes=False) == ast.dump(
                ast.parse(right), include_attributes=False
            )
        except SyntaxError:
            return left.strip() == right.strip()

    def diverged(self):
        result = {}
        for (module, name), live_source in sorted(self._live_sources.items()):
            disk_source, path, _ = self._disk_definition(module, name)
            if self._same_definition(disk_source, live_source):
                continue
            before = [] if disk_source is None else disk_source.splitlines()
            after = [] if live_source is None else live_source.splitlines()
            diff = "\n".join(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=str(path or f"{module} (disk)"),
                    tofile=f"{module}.{name} (live)",
                    lineterm="",
                )
            )
            result[f"{module}.{name}"] = diff
        for qualified, diff in result.items():
            print(f"## {qualified}\n{diff}")
        return result

    def source_patch(self):
        return self.diverged()

    def persist_source(self, target=None):
        selected = None if target is None else self._resolve_target(target)
        changes = []
        for key, live_source in self._live_sources.items():
            if selected is not None and key != selected:
                continue
            module, name = key
            disk_source, path, lines = self._disk_definition(module, name)
            if path is None:
                raise ValueError(f"{module}.{name} has no project source file")
            base_source = self._source_bases.get(key, disk_source)
            if not self._same_definition(disk_source, base_source):
                raise SourceConflictError(
                    f"Refusing to overwrite externally changed definition "
                    f"{module}.{name}"
                )
            changes.append((path, lines, live_source, module, name))

        by_path = {}
        for change in changes:
            by_path.setdefault(change[0], []).append(change)
        persisted = []
        for path, file_changes in by_path.items():
            lines = path.read_text(encoding="utf-8").splitlines()
            ordered = sorted(
                file_changes,
                key=lambda change: change[1][0] if change[1] else len(lines) + 1,
                reverse=True,
            )
            for _, span, source_text, module, name in ordered:
                replacement = [] if source_text is None else source_text.splitlines()
                if span is None:
                    if replacement:
                        if lines and lines[-1]:
                            lines.append("")
                        lines.extend(replacement)
                else:
                    start, end = span
                    lines[start - 1 : end] = replacement
                persisted.append(f"{module}.{name}")
                self._record_event("persist", module, name, str(path))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        persisted_modules = {module for _, _, _, module, _ in changes}
        for key in list(self._live_sources):
            if selected is None or key == selected:
                self._live_sources.pop(key, None)
                self._source_bases.pop(key, None)
        for module in persisted_modules:
            self._remember_module_source(module)
        print("Persisted:", ", ".join(sorted(persisted)) or "(none)")
        return tuple(sorted(persisted))
