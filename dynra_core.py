import sys
import types
import builtins
import os
import importlib
import gc
import ast


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
                        # We want the 'deepest' node that contains the line but is still a top-level def
                        # Actually for CIDER, we usually want the top-level one.
                        if (
                            node.lineno < target_node.lineno
                            or node.end_lineno > target_node.end_lineno
                        ):
                            target_node = node

    if target_node:
        lines = source.splitlines()
        return "\n".join(lines[target_node.lineno - 1 : target_node.end_lineno])
    return None


def patch_function(old_func, new_func):
    """Update the code and metadata of an existing function object."""
    attrs = ["__code__", "__defaults__", "__doc__", "__annotations__", "__kwdefaults__"]
    for attr in attrs:
        try:
            setattr(old_func, attr, getattr(new_func, attr))
        except (AttributeError, TypeError):
            pass


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


class NamespaceManager:
    def __init__(self, shell=None):
        self.shell = shell
        self.current_name = "user"
        self.safe_class_patching = True
        self.prev_ns_snapshot = {}
        self._create_module("user")

    def _create_module(self, name):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__file__ = f"<repl:{name}>"
            sys.modules[name] = mod
            mod.__dict__.update(
                {
                    "__name__": name,
                    "__builtins__": builtins,
                    "__package__": "",
                    "__doc__": None,
                    "__loader__": None,
                    "__spec__": None,
                    "md": self.md,
                    "in_md": self.in_md,
                    "ls_md": self.ls_md,
                    "help_dynra": self.help_dynra,
                    "doc": self.doc,
                    "source": self.source,
                    "dir_md": self.dir_md,
                    "find_var": self.find_var,
                    "class_patch_mode": self.class_patch_mode,
                }
            )
        return sys.modules[name]

    def _init_ipython_ns(self, mod_dict):
        if self.shell:
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
            for k, v in infra.items():
                if k not in mod_dict or k in ["In", "Out", "_oh", "_ih"]:
                    mod_dict[k] = v

    def md(self, name):
        """Create (or get) a REPL module without switching to it."""
        return self._create_module(name)

    def doc(self, obj):
        """Print the documentation for an object."""
        print(
            importlib.import_module("inspect").getdoc(obj) or "No documentation found."
        )

    def source(self, obj):
        """Print the source code for an object."""
        try:
            print(importlib.import_module("inspect").getsource(obj))
        except Exception as e:
            print(f"Could not get source: {e}")

    def dir_md(self, module=None):
        """List public symbols in the current or specified module."""
        if module is None:
            if self.shell:
                target = self.shell.user_ns
            else:
                target = globals()
        elif isinstance(module, str):
            target = sys.modules.get(module).__dict__ if module in sys.modules else {}
        else:
            target = module.__dict__

        publics = [k for k in target.keys() if not k.startswith("_")]
        print("\n".join(sorted(publics)))

    def find_var(self, pattern):
        """Find variables matching a pattern across all modules."""
        import re

        results = []
        for mod_name, mod in sys.modules.items():
            if not isinstance(mod, types.ModuleType):
                continue
            for attr in mod.__dict__:
                if re.search(pattern, attr):
                    results.append(f"{mod_name}/{attr}")
        print("\n".join(sorted(results)))

    def class_patch_mode(self, mode=None):
        """Get/set class patch mode: 'safe' (default) or 'permissive'."""
        if mode is None:
            current = "safe" if self.safe_class_patching else "permissive"
            print(f"class_patch_mode: {current}")
            return current

        mode = str(mode).strip().lower()
        if mode in ("safe", "on", "true", "1"):
            self.safe_class_patching = True
        elif mode in ("permissive", "off", "false", "0"):
            self.safe_class_patching = False
        else:
            print("Invalid mode. Use 'safe' or 'permissive'.")
            return None

        current = "safe" if self.safe_class_patching else "permissive"
        print(f"class_patch_mode set to: {current}")
        return current

    def in_md(self, name):
        # First, try to import the real module if not already loaded
        if name not in sys.modules:
            try:
                importlib.import_module(name)
            except ImportError:
                pass  # Will create REPL module below if import fails

        self.current_name = name
        mod = sys.modules.get(name)

        # If module is a REPL shadow (no real file), try harder to find the real module
        # This handles packages that exist on disk but haven't been imported yet
        if (
            mod
            and hasattr(mod, "__file__")
            and mod.__file__
            and mod.__file__.startswith("<repl:")
        ):
            # Try importing from the actual path
            try:
                real_mod = importlib.import_module(name)
                if real_mod:
                    mod = real_mod
                    sys.modules[name] = mod  # Replace the REPL module with real one
            except ImportError:
                pass  # Keep the REPL module

        if mod is None:
            # No real module exists - create REPL module
            self._create_module(name)
            mod = sys.modules.get(name)

        if mod:
            # Crucially, we must ensure even imported modules have our tools
            # and proper builtins if they are being used as active namespaces
            mod.__dict__.update(
                {
                    "md": self.md,
                    "in_md": self.in_md,
                    "ls_md": self.ls_md,
                    "help_dynra": self.help_dynra,
                    "doc": self.doc,
                    "source": self.source,
                    "dir_md": self.dir_md,
                    "find_var": self.find_var,
                    "class_patch_mode": self.class_patch_mode,
                }
            )

            if self.shell:
                self._init_ipython_ns(mod.__dict__)
                self.shell.user_ns = mod.__dict__
                self.shell.user_module = mod
                print(f"Switched to module '{name}'")
        else:
            print(f"Error: Module '{name}' could not be created/found.")

    def ls_md(self):
        repl_mods = [
            n
            for n, m in sys.modules.items()
            if hasattr(m, "__file__") and m.__file__ and m.__file__.startswith("<repl:")
        ]
        proj_root = os.getcwd()
        proj_mods = [
            n
            for n, m in sys.modules.items()
            if hasattr(m, "__file__")
            and m.__file__
            and m.__file__.startswith(proj_root)
        ]
        print("REPL Modules:", ", ".join(repl_mods))
        print("Project Modules:", ", ".join(proj_mods))

    def help_dynra(self):
        print("""
dynra commands:
  md(name)      - Create/get a REPL module (no switch)
  in_md(name)   - Switch to a module (creates if needed)
  ls_md()       - List project and REPL modules
  dir_md([mod]) - List public symbols in current or specific module
  doc(obj)      - Show documentation for an object
  source(obj)   - Show source code for an object
  find_var(pat) - Find symbols matching regex pattern
  class_patch_mode([mode]) - Get/set class patch mode: safe|permissive
  help_dynra()   - Show this help
        """)

    def pre_execute(self):
        if self.shell:
            self.prev_ns_snapshot = {k: id(v) for k, v in self.shell.user_ns.items()}

    def post_execute(self):
        if not self.shell:
            return
        current_ns = self.shell.user_ns
        for name, obj in current_ns.items():
            if name.startswith("_") or name in (
                "In",
                "Out",
                "get_ipython",
                "exit",
                "quit",
                "md",
                "in_md",
                "ls_md",
                "help_dynra",
            ):
                continue
            if name in self.prev_ns_snapshot and id(obj) != self.prev_ns_snapshot[name]:
                # Only propagate if THIS module is the one that DEFINES the object.
                # This prevents circular/redundant propagation when we patch imported objects.
                obj_mod = getattr(obj, "__module__", None)
                current_mod_name = getattr(self.shell.user_module, "__name__", None)
                if obj_mod == current_mod_name:
                    self._propagate_update(name, obj, current_mod_name)

    def _propagate_update(self, name, new_obj, origin_mod_name):
        for mod_name, mod in sys.modules.items():
            if mod_name == origin_mod_name or not isinstance(mod, types.ModuleType):
                continue
            if name in mod.__dict__:
                old_obj = mod.__dict__[name]
                if (
                    hasattr(old_obj, "__module__")
                    and old_obj.__module__ == origin_mod_name
                ):
                    if isinstance(new_obj, types.FunctionType) and isinstance(
                        old_obj, types.FunctionType
                    ):
                        patch_function(old_obj, new_obj)
                    elif isinstance(new_obj, type) and isinstance(old_obj, type):
                        patch_class(
                            old_obj,
                            new_obj,
                            safe_mode=self.safe_class_patching,
                        )
