import socket
import sys

import pexpect

from dynra.mcp import (
    dynra_bootstrap,
    dynra_bootstrap_status,
    dynra_diff,
    dynra_evaluate,
    dynra_modules,
    dynra_events,
    dynra_patch_log,
    dynra_persist,
    dynra_repl,
    dynra_revert,
    dynra_source,
    dynra_status,
    dynra_symbols,
)


def free_ports(count):
    listeners = [socket.socket() for _ in range(count)]
    try:
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        return tuple(listener.getsockname()[1] for listener in listeners)
    finally:
        for listener in listeners:
            listener.close()


def start_server(port):
    server = pexpect.spawn(
        sys.executable,
        ["-m", "dynra", "--port", str(port)],
        encoding="utf-8",
    )
    server.expect(r"user> ", timeout=10)
    return server


def test_single_tool_agent_workflow():
    port = free_ports(1)[0]
    server = start_server(port)

    def call(**arguments):
        return dynra_repl(port=port, **arguments)

    try:
        response = call(code="1 + 1")
        assert response == {"success": True, "result": "2"}

        response = call(
            module="agent_lib",
            code="def get_value(): return 1",
            create=True,
        )
        assert response["success"]
        assert response["source_sync"] == {
            "status": "live-newer",
            "generation": 1,
            "live": ["agent_lib.get_value"],
        }

        response = call(
            module="agent_app",
            code="from agent_lib import get_value",
            create=True,
        )
        assert response["success"]
        response = call(module="agent_app", code="get_value()")
        assert response["result"] == "1"

        response = call(
            module="agent_lib",
            code="def get_value(): return 100",
        )
        assert response["success"]
        response = call(module="agent_app", code="get_value()")
        assert response["result"] == "100"

        response = call(
            module="agent_app", code="print('visible output')"
        )
        assert response == {"success": True, "stdout": "visible output"}

        response = call(module="invalid-module", code="x = 1")
        assert response == {
            "success": False,
            "error": "Invalid Python module name: 'invalid-module'",
        }

        response = call(code="1 / 0")
        assert not response["success"]
        assert "ZeroDivisionError" in response["stdout"]
        assert response["error"] == "ZeroDivisionError: division by zero"
    finally:
        server.terminate(force=True)


def test_agent_selects_between_long_running_runtime_ports():
    first_port, second_port = free_ports(2)
    first_server = start_server(first_port)
    second_server = start_server(second_port)

    try:
        assert dynra_repl(code="runtime_id = 'first'", port=first_port)["success"]
        assert dynra_repl(code="runtime_id = 'second'", port=second_port)["success"]

        first = dynra_repl(code="runtime_id", port=first_port)
        second = dynra_repl(code="runtime_id", port=second_port)

        assert first["result"] == "'first'"
        assert second["result"] == "'second'"
    finally:
        first_server.terminate(force=True)
        second_server.terminate(force=True)


def test_agent_rejects_invalid_runtime_ports():
    for port in (0, 65536, True, "9999"):
        response = dynra_repl(code="1 + 1", port=port)

        assert response["success"] is False
        assert set(response) == {"success", "error"}
        assert "Invalid port" in response["error"]


def test_agent_rejects_empty_code_without_response_noise():
    assert dynra_repl(code=" \n ") == {
        "success": False,
        "error": "Code must not be empty.",
    }


def test_structured_runtime_tools(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_file = package / "mod.py"
    source_file.write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.dynra]\nbootstrap = 'pkg.boot:load'\n",
        encoding="utf-8",
    )
    (package / "boot.py").write_text(
        "def load(namespace):\n    return {'booted': 42}\n",
        encoding="utf-8",
    )
    port = free_ports(1)[0]
    server = pexpect.spawn(
        sys.executable,
        ["-m", "dynra", "--port", str(port)],
        cwd=str(tmp_path),
        encoding="utf-8",
    )
    server.expect(r"user> ", timeout=10)
    try:
        status = dynra_status(port=port)
        assert status["success"] is True
        assert status["runtime"]["project_root"] == str(tmp_path.resolve())

        modules = dynra_modules(port=port)
        assert "pkg.mod" in modules["result"]["discovered"]
        assert dynra_symbols("pkg.mod", port=port)["result"] == ["value"]
        assert "def value()" in dynra_source("pkg.mod.value", port=port)["result"]
        bootstrap_status = dynra_bootstrap_status(port=port)
        assert bootstrap_status["result"]["completed"] is False
        assert dynra_bootstrap(port=port)["result"]["completed"] is True
        assert dynra_evaluate(code="booted", port=port)["result"] == "42"

        patched = dynra_evaluate(
            module="pkg.mod",
            code="def value():\n    return 2",
            port=port,
        )
        assert patched["success"] is True
        assert "pkg.mod.value" in dynra_diff(port=port)["result"]
        assert dynra_patch_log(port=port)["result"][-1]["action"] == "patch"
        events = dynra_events(port=port)
        assert events["result"][-1]["action"] == "patch"
        cursor = events["result"][-1]["sequence"]
        assert dynra_events(after=cursor, port=port)["result"] == []

        reverted = dynra_revert("pkg.mod.value", port=port)
        assert reverted["result"] == "pkg.mod.value"
        assert dynra_evaluate(module="pkg.mod", code="value()", port=port)["result"] == "1"

        dynra_evaluate(
            module="pkg.mod",
            code="def value():\n    return 3",
            port=port,
        )
        persisted = dynra_persist("pkg.mod.value", port=port)
        assert persisted["result"] == ["pkg.mod.value"]
        assert "return 3" in source_file.read_text(encoding="utf-8")
    finally:
        server.terminate(force=True)
