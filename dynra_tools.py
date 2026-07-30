"""Dynra inspection tools - available in regular Python."""

import sys
import types
import re
import inspect
import importlib


def doc(obj):
    """Print the documentation for an object."""
    print(inspect.getdoc(obj) or "No documentation found.")


def source(obj):
    """Print the source code for an object."""
    try:
        print(inspect.getsource(obj))
    except Exception as e:
        print(f"Could not get source: {e}")


def dir_md(module=None):
    """List public symbols in the current or specified module."""
    if module is None:
        target = globals()
    elif isinstance(module, str):
        target = sys.modules.get(module).__dict__ if module in sys.modules else {}
    else:
        target = module.__dict__

    publics = [k for k in target.keys() if not k.startswith("_")]
    print("\n".join(sorted(publics)))


def find_var(pattern):
    """Find variables matching a pattern across all modules."""
    results = []
    for mod_name, mod in sys.modules.items():
        if not isinstance(mod, types.ModuleType):
            continue
        for attr in mod.__dict__:
            if re.search(pattern, attr):
                results.append(f"{mod_name}/{attr}")
    print("\n".join(sorted(results)))


def help_dynra():
    """Show Dynra commands help."""
    print("""
dynra commands:
  doc(obj)      - Show documentation for an object
  source(obj)   - Show source code for an object
  dir_md([mod]) - List public symbols in current or specific module
  find_var(pat) - Find symbols matching regex pattern
  help_dynra()   - Show this help
    """)


if __name__ == "__main__":
    help_dynra()
