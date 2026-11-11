import asyncio
import builtins
import sys

import pytest
from IPython.core.interactiveshell import InteractiveShell

from dynra.core import NamespaceManager, SourceConflictError


def _attach_shell(manager, module):
    shell = InteractiveShell()
    manager.shell = shell
    manager._init_ipython_ns(module.__dict__)
    shell.user_ns = module.__dict__
    shell.user_module = module
    shell.events.register("pre_execute", manager.pre_execute)
    shell.events.register("post_execute", manager.post_execute)
    return shell


def test_src_layout_discovery_is_strict_and_does_not_pollute_modules(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '1.0'\n",
        encoding="utf-8",
    )
    package = tmp_path / "src" / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "api.py").write_text(
        "VALUE = 3\ndef answer():\n    return VALUE\n",
        encoding="utf-8",
    )

    manager = NamespaceManager(project_root=tmp_path)
    try:
        catalog = manager.module_catalog()
        assert "sample.api" in catalog["discovered"]
        assert "sample.api" not in catalog["loaded_project"]
        assert manager.dir_md("sample.api") == ("VALUE", "answer")

        module = manager.in_md("sample.api")
        assert module.answer() == 3
        assert "in_md" not in module.__dict__
        assert builtins.in_md.__self__ is manager

        with pytest.raises(ModuleNotFoundError):
            manager.in_md("sample.typo")
        synthetic = manager.create_md("scratch_space")
        assert synthetic.__file__ == "<repl:scratch_space>"
        assert "scratch_space" in manager.module_catalog()["synthetic"]
    finally:
        sys.modules.pop("sample.api", None)
        sys.modules.pop("sample", None)
        sys.modules.pop("scratch_space", None)

def test_new_project_module_is_discovered_without_restarting_runtime(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '1.0'\n",
        encoding="utf-8",
    )
    manager = NamespaceManager(project_root=tmp_path)
    module_name = "module2"
    try:
        assert module_name not in manager.module_catalog()["discovered"]
        (tmp_path / "module2.py").write_text("VALUE = 42\n", encoding="utf-8")

        module = manager.in_md(module_name)

        assert module.VALUE == 42
        assert module_name in manager.module_catalog()["discovered"]
    finally:
        sys.modules.pop(module_name, None)



def test_statements_observe_redefinitions_immediately_and_skip_old_decorators(tmp_path):
    manager = NamespaceManager(project_root=tmp_path)
    module = manager.create_md("dynra_sequential")
    _attach_shell(manager, module)
    try:
        initial = manager.execute_code(
            "registry = {}\n"
            "def route(fn):\n"
            "    registry['handler'] = fn\n"
            "    return fn\n"
            "@route\n"
            "def handler():\n"
            "    return 1\n"
        )
        assert initial.success
        original = module.handler
        assert module.registry["handler"] is original

        changed = manager.execute_code(
            "@route\n"
            "def handler():\n"
            "    return 2\n"
            "observed = registry['handler']()\n"
        )
        assert changed.success
        assert module.handler is original
        assert module.registry["handler"] is original
        assert module.observed == 2
    finally:
        sys.modules.pop("dynra_sequential", None)


@pytest.mark.parametrize("name", ["_add", "__add__"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_private_function_update_reaches_existing_imports(tmp_path, name, asynchronous):
    manager = NamespaceManager(project_root=tmp_path)
    origin = manager.create_md("dynra_private_origin")
    consumer = manager.create_md("dynra_private_consumer")
    shell = _attach_shell(manager, origin)
    prefix = "async " if asynchronous else ""

    def invoke(function):
        result = function(1, 1)
        return asyncio.run(result) if asynchronous else result

    try:
        assert manager.execute_code(
            f"{prefix}def {name}(x, y):\n    return x + y"
        ).success
        exec(f"from {origin.__name__} import {name} as calculate", consumer.__dict__)
        original = consumer.calculate
        assert invoke(original) == 2

        # Evaluating a function object also puts it in the shell's `_` cache.
        assert shell.run_cell(name).success
        assert manager.execute_code(
            f"{prefix}def {name}(x, y):\n    return x + y + x"
        ).success

        assert consumer.calculate is original
        assert getattr(origin, name) is original
        assert invoke(consumer.calculate) == 3
        assert invoke(shell.user_ns["_"]) == 3

        manager.revert(f"{origin.__name__}.{name}")
        assert invoke(consumer.calculate) == 2
    finally:
        sys.modules.pop(origin.__name__, None)
        sys.modules.pop(consumer.__name__, None)


def test_private_class_update_reaches_existing_instance(tmp_path):
    manager = NamespaceManager(project_root=tmp_path)
    module = manager.create_md("dynra_private_class")
    _attach_shell(manager, module)
    try:
        assert manager.execute_code(
            "class _Basket:\n"
            "    def __init__(self):\n        self.prices = [10, 20]\n"
            "    def total(self):\n        return sum(self.prices)\n"
            "basket = _Basket()\n"
        ).success
        original = module._Basket
        instance = module.basket

        assert manager.execute_code(
            "class _Basket:\n"
            "    def total(self):\n        return sum(self.prices) + 1\n"
        ).success

        assert module._Basket is original
        assert module.basket is instance
        assert instance.prices == [10, 20]
        assert instance.total() == 31
    finally:
        sys.modules.pop(module.__name__, None)


def test_failed_multi_definition_patch_rolls_back_every_definition(tmp_path):
    manager = NamespaceManager(project_root=tmp_path)
    module = manager.create_md("dynra_transaction")
    _attach_shell(manager, module)
    try:
        assert manager.execute_code(
            "def first():\n"
            "    return 1\n"
            "async def second():\n"
            "    return 1\n"
            "registry = [second]\n"
        ).success
        original_first = module.first
        original_second = module.second

        result = manager.execute_code(
            "def first():\n"
            "    return 2\n"
            "def second():\n"
            "    return 2\n"
        )

        assert not result.success
        assert module.first is original_first
        assert module.first() == 1
        assert module.second is original_second
        assert module.registry[0] is original_second
        assert manager.history[-1]["action"] == "transaction"
        events = manager.events()
        failed_events = events[-2:]
        assert failed_events[0]["action"] == "refused"
        assert failed_events[1]["action"] == "transaction"
        assert failed_events[1]["status"] == "rolled-back"
        assert manager.events(after=failed_events[0]["sequence"], limit=1) == (
            failed_events[1],
        )
        mutated = manager.events()[-1]
        mutated["detail"]["result"] = "changed"
        assert manager.events()[-1]["detail"]["result"] == "rolled-back"
    finally:
        sys.modules.pop("dynra_transaction", None)


def test_revert_unmap_divergence_and_source_persistence(tmp_path):
    package = tmp_path / "src" / "persistpkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_file = package / "logic.py"
    source_file.write_text(
        "def value():\n    return 1\n\ndef untouched():\n    return 9\n",
        encoding="utf-8",
    )

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.in_md("persistpkg.logic")
    _attach_shell(manager, module)
    manager.in_md("persistpkg.logic")
    try:
        original = module.value
        assert manager.execute_code("def value():\n    return 2\n").success
        assert module.value is original
        assert module.value() == 2
        assert "persistpkg.logic.value" in manager.diverged()

        manager.revert("value")
        assert module.value is original
        assert module.value() == 1
        assert manager.diverged() == {}
        assert manager.source_sync_status()["status"] == "clean"

        assert manager.execute_code("def value():\n    return 3\n").success
        assert manager.persist_source("value") == ("persistpkg.logic.value",)
        assert "return 3" in source_file.read_text(encoding="utf-8")
        assert "return 9" in source_file.read_text(encoding="utf-8")
        assert manager.diverged() == {}

        registry = [module.value]
        module.value_alias = module.value
        holders = manager.unmap("value")
        assert "list" in holders
        assert "persistpkg.logic.value_alias" in holders
        assert "value" not in module.__dict__
        assert registry[0]() == 3
        assert module.value_alias is registry[0]
        assert any(entry["action"] == "unmap" for entry in manager.patch_log())
        manager.revert("persistpkg.logic.value")
        assert module.value is registry[0]
    finally:
        sys.modules.pop("persistpkg.logic", None)
        sys.modules.pop("persistpkg", None)


def test_object_revert_uses_definition_owner_instead_of_public_module(tmp_path):
    package = tmp_path / "revertpkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from .logic import Exported\nExported.__module__ = __name__\n",
        encoding="utf-8",
    )
    (package / "logic.py").write_text(
        "class Exported:\n    def value(self):\n        return 1\n",
        encoding="utf-8",
    )

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.in_md("revertpkg.logic")
    _attach_shell(manager, module)
    manager.in_md("revertpkg.logic")
    try:
        exported = module.Exported
        assert exported.__module__ == "revertpkg"
        assert manager.execute_code(
            "class Exported:\n"
            "    def value(self):\n"
            "        return 2\n"
        ).success
        assert exported().value() == 2

        assert manager.revert(exported) is exported
        assert exported().value() == 1
    finally:
        sys.modules.pop("revertpkg.logic", None)
        sys.modules.pop("revertpkg", None)

def test_external_definition_edits_autoheal_without_reloading_importers(tmp_path):
    package = tmp_path / "src" / "healpkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_file = package / "logic.py"
    source_file.write_text(
        "events = []\n"
        "CONSTANT = 1\n"
        "def value():\n"
        "    return 1\n\n"
        "def removed():\n"
        "    return 'old'\n\n"
        "def __dynra_reconcile__(changes):\n"
        "    events.append(changes)\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from healpkg.logic import removed, value\n",
        encoding="utf-8",
    )

    manager = NamespaceManager(project_root=tmp_path)
    consumer = manager.in_md("healpkg.consumer")
    logic = manager.in_md("healpkg.logic")
    _attach_shell(manager, logic)
    manager.in_md("healpkg.logic")
    original = logic.value
    try:
        source_file.write_text(
            "events = []\n"
            "CONSTANT = 2\n"
            "def value():\n"
            "    return 2\n\n"
            "def added():\n"
            "    return 'new'\n\n"
            "def __dynra_reconcile__(changes):\n"
            "    events.append(changes)\n",
            encoding="utf-8",
        )

        execution = manager.execute_code("value()")
        assert execution.success
        assert execution.result == 2

        assert logic.value is original
        assert consumer.value is original
        assert consumer.value() == 2
        assert logic.added() == "new"
        assert not hasattr(logic, "removed")
        assert not hasattr(consumer, "removed")
        assert logic.CONSTANT == 1
        assert manager.last_source_sync["status"] == "healed"
        assert manager.last_source_sync["generation"] == 1
        assert {change["action"] for change in logic.events[-1]} == {
            "added",
            "removed",
            "updated",
        }
    finally:
        for name in ("healpkg.consumer", "healpkg.logic", "healpkg"):
            sys.modules.pop(name, None)


def test_external_sync_applies_declared_literals_and_reports_unsafe_changes(tmp_path):
    package = tmp_path / "src" / "safesync"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_file = package / "logic.py"
    source_file.write_text(
        "import math\n"
        "calls = []\n"
        "def decorate(fn):\n"
        "    calls.append(fn.__name__)\n"
        "    return fn\n"
        "__dynra_reloadable__ = ('SETTING', 'BAD')\n"
        "SETTING = 1\n"
        "BAD = 2\n"
        "UNDECLARED = 1\n"
        "@decorate\n"
        "def value():\n"
        "    return SETTING\n",
        encoding="utf-8",
    )

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.in_md("safesync.logic")
    _attach_shell(manager, module)
    manager.in_md("safesync.logic")
    try:
        source_file.write_text(
            "import json\n"
            "calls = []\n"
            "def decorate(fn):\n"
            "    calls.append(fn.__name__)\n"
            "    return fn\n"
            "def make_bad():\n"
            "    raise RuntimeError('must not run')\n"
            "__dynra_reloadable__ = ('SETTING', 'BAD')\n"
            "SETTING = 3\n"
            "BAD = make_bad()\n"
            "UNDECLARED = 9\n"
            "@missing_decorator\n"
            "def value():\n"
            "    return SETTING\n",
            encoding="utf-8",
        )

        manager.in_md("safesync.logic")

        report = manager.last_source_sync
        assert report["status"] == "restart-required"
        assert module.SETTING == 3
        assert module.BAD == 2
        assert module.UNDECLARED == 1
        assert module.value() == 3
        assert module.calls == ["value"]
        assert "json" not in module.__dict__
        assert {item["kind"] for item in report["restart_required"]} == {
            "constant",
            "decorators",
            "imports",
        }
        assert any(
            event["action"] == "source-sync"
            and event["status"] == "restart-required"
            for event in manager.events()
        )
    finally:
        for name in ("safesync.logic", "safesync"):
            sys.modules.pop(name, None)

def test_external_edit_conflicts_with_live_patch_and_blocks_persistence(tmp_path):
    package = tmp_path / "src" / "conflictpkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_file = package / "logic.py"
    source_file.write_text("def value():\n    return 1\n", encoding="utf-8")

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.in_md("conflictpkg.logic")
    _attach_shell(manager, module)
    manager.in_md("conflictpkg.logic")
    try:
        assert manager.execute_code("def value():\n    return 2\n").success
        assert manager.module_generation() == 1
        source_file.write_text("def value():\n    return 3\n", encoding="utf-8")

        manager.in_md("conflictpkg.logic")

        assert module.value() == 2
        assert manager.last_source_sync["status"] == "conflict"
        assert manager.last_source_sync["conflicts"] == (
            "conflictpkg.logic.value",
        )
        with pytest.raises(SourceConflictError):
            manager.persist_source("value")
        assert "return 3" in source_file.read_text(encoding="utf-8")
    finally:
        for name in ("conflictpkg.logic", "conflictpkg"):
            sys.modules.pop(name, None)
