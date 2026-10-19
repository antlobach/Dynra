import socket
import sys

import pexpect

from dynra.mcp import dynra_repl


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
    server = pexpect.spawn(
        sys.executable, ["-m", "dynra"], encoding="utf-8"
    )

    try:
        server.expect(r"user> ", timeout=10)

        response = dynra_repl(code="1 + 1")
        assert response == {"success": True, "result": "2"}

        response = dynra_repl(
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

        response = dynra_repl(
            module="agent_app",
            code="from agent_lib import get_value",
            create=True,
        )
        assert response["success"]
        response = dynra_repl(module="agent_app", code="get_value()")
        assert response["result"] == "1"

        response = dynra_repl(
            module="agent_lib",
            code="def get_value(): return 100",
        )
        assert response["success"]
        response = dynra_repl(module="agent_app", code="get_value()")
        assert response["result"] == "100"

        response = dynra_repl(
            module="agent_app", code="print('visible output')"
        )
        assert response == {"success": True, "stdout": "visible output"}

        response = dynra_repl(module="invalid-module", code="x = 1")
        assert response == {
            "success": False,
            "error": "Invalid Python module name: 'invalid-module'",
        }

        response = dynra_repl(code="1 / 0")
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
