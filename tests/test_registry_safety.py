"""Redefinition must reach objects captured into registries, or refuse loudly.

A web framework stores the handler *object* in a route table at startup. If a
redefinition rebinds the module name instead of updating that object, the edit
looks like it worked and the server keeps serving the old code. These tests pin
both halves of the contract: patch in place when it is safe, refuse when it is
not.
"""

import sys

from dynra.core import (
    NamespaceManager,
    analyze_function_patch_compatibility,
    direct_holders,
)


class _Shell:
    """Minimal stand-in for the IPython shell the manager drives."""

    def __init__(self, module):
        self.user_module = module
        self.user_ns = module.__dict__


def _redefine(manager, module, source):
    """One edit cycle, the way the REPL server performs it."""
    manager.pre_execute()
    exec(source, module.__dict__)
    manager.post_execute()


def test_redefinition_preserves_object_identity():
    manager = NamespaceManager()
    name = "dynra_test_identity"
    module = manager.create_md(name)
    manager.shell = _Shell(module)

    try:
        exec("def handler(): return 1", module.__dict__)
        original = module.handler

        _redefine(manager, module, "def handler(): return 2")

        assert module.handler is original, "redefinition must not rebind the name"
        assert module.handler() == 2
    finally:
        sys.modules.pop(name, None)


def test_registry_held_function_sees_new_code():
    """The route-table case: a list captured the function before the edit."""
    manager = NamespaceManager()
    name = "dynra_test_registry"
    module = manager.create_md(name)
    manager.shell = _Shell(module)

    try:
        exec("def handler(): return 'v1'", module.__dict__)
        registry = [module.handler]

        _redefine(manager, module, "def handler(): return 'v2'")

        assert registry[0]() == "v2", "registry kept running the old code"
    finally:
        sys.modules.pop(name, None)


def test_incompatible_redefinition_of_held_function_is_refused():
    manager = NamespaceManager()
    name = "dynra_test_refusal"
    module = manager.create_md(name)
    manager.shell = _Shell(module)

    try:
        exec("async def handler(): return 'v1'", module.__dict__)
        original = module.handler
        registry = [module.handler]

        # async -> sync cannot be applied in place.
        _redefine(manager, module, "def handler(): return 'v2'")

        assert module.handler is original, "refused patch must leave the old binding"
        assert registry[0] is original
    finally:
        sys.modules.pop(name, None)


def test_permissive_mode_allows_the_rebind():
    manager = NamespaceManager()
    name = "dynra_test_permissive"
    module = manager.create_md(name)
    manager.shell = _Shell(module)
    manager.function_patch_mode("permissive")

    try:
        exec("async def handler(): return 'v1'", module.__dict__)
        original = module.handler
        _registry = [module.handler]

        _redefine(manager, module, "def handler(): return 'v2'")

        assert module.handler is not original, "permissive mode should rebind"
        assert module.handler() == "v2"
    finally:
        sys.modules.pop(name, None)


def test_compatibility_analysis_reports_reasons():
    def sync_fn():
        return 1

    async def async_fn():
        return 1

    assert analyze_function_patch_compatibility(sync_fn, sync_fn) == []
    assert any(
        "function kind changed" in reason
        for reason in analyze_function_patch_compatibility(sync_fn, async_fn)
    )
    assert analyze_function_patch_compatibility(sync_fn, "not a function")


def test_direct_holders_ignores_module_namespaces():
    name = "dynra_test_holders"
    manager = NamespaceManager()
    module = manager.create_md(name)
    try:
        exec("def handler(): return 1", module.__dict__)
        fn = module.handler

        assert direct_holders(fn) == [], "a module namespace is not a registry"

        registry = [fn]
        assert "list" in direct_holders(fn)
        del registry
    finally:
        sys.modules.pop(name, None)
