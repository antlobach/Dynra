import sys

import pytest

from dynra.core import NamespaceManager


def write_project(tmp_path, target, source):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='1'\n"
        f"[tool.dynra]\nbootstrap='{target}'\n",
        encoding="utf-8",
    )
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "bootstrap.py").write_text(source, encoding="utf-8")


def clear_modules():
    for name in ("sample.bootstrap", "sample"):
        sys.modules.pop(name, None)


def test_bootstrap_is_explicit_exports_state_and_does_not_change_module(tmp_path):
    write_project(
        tmp_path,
        "sample.bootstrap:prepare",
        "import builtins\n"
        "builtins.bootstrap_imported = True\n"
        "calls = 0\n"
        "def prepare(namespace):\n"
        "    global calls\n"
        "    calls += 1\n"
        "    namespace['direct'] = calls\n"
        "    return {'service': f'service-{calls}'}\n",
    )
    if hasattr(__import__("builtins"), "bootstrap_imported"):
        delattr(__import__("builtins"), "bootstrap_imported")
    manager = NamespaceManager(project_root=tmp_path)
    try:
        assert manager.project.config.bootstrap == "sample.bootstrap:prepare"
        assert "sample.bootstrap" not in sys.modules
        assert manager.bootstrap_status()["completed"] is False
        assert manager.current_name == "user"

        result = manager.bootstrap()
        assert result["completed"] is True
        assert result["exported"] == ("service",)
        assert sys.modules["user"].direct == 1
        assert sys.modules["user"].service == "service-1"
        assert manager.current_name == "user"

        repeated = manager.bootstrap()
        assert repeated["already_completed"] is True
        assert sys.modules["user"].direct == 1

        forced = manager.bootstrap(force=True)
        assert forced["attempts"] == 2
        assert sys.modules["user"].service == "service-2"
    finally:
        clear_modules()
        if hasattr(__import__("builtins"), "bootstrap_imported"):
            delattr(__import__("builtins"), "bootstrap_imported")


def test_bootstrap_module_import_and_failures_are_explicit(tmp_path):
    write_project(tmp_path, "sample.bootstrap", "VALUE = 1\n")
    manager = NamespaceManager(project_root=tmp_path)
    try:
        assert "sample.bootstrap" not in sys.modules
        result = manager.bootstrap()
        assert result["module"] == "sample.bootstrap"
        assert result["callable"] is None
    finally:
        clear_modules()

    manager = NamespaceManager(project_root=tmp_path / "unconfigured")
    with pytest.raises(ValueError, match="No .*bootstrap target"):
        manager.bootstrap()


def test_bootstrap_rejects_non_mapping_result(tmp_path):
    write_project(
        tmp_path,
        "sample.bootstrap:prepare",
        "def prepare(namespace):\n    return 3\n",
    )
    manager = NamespaceManager(project_root=tmp_path)
    try:
        with pytest.raises(TypeError, match="mapping or None"):
            manager.bootstrap()
        status = manager.bootstrap_status()
        assert status["completed"] is False
        assert status["attempts"] == 1
    finally:
        clear_modules()
