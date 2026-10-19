import socket
import sys

import pexpect

from dynra_mcp import dynra_repl


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
        ["dynra.py", "--port", str(port)],
        encoding="utf-8",
    )
    server.expect(r"user> ", timeout=10)
    return server


def test_single_tool_agent_workflow():
    server = pexpect.spawn(sys.executable, ["dynra.py"], encoding="utf-8")

    try:
        server.expect(r"user> ", timeout=10)

        response = dynra_repl(code="1 + 1")
        assert response["success"]
        assert response["module"] == "user"
        assert response["result"] == "2"
        assert response["generation"] == 0
        assert response["source_sync"]["status"] == "clean"

        response = dynra_repl(
            module="agent_lib",
            code="def get_value(): return 1",
            create=True,
        )
        assert response["success"]
        assert response["generation"] == 1
        assert response["source_sync"]["status"] == "live-newer"

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

        response = dynra_repl(module="invalid-module", code="x = 1")
        assert not response["success"]
        assert "Invalid Python module name" in response["error"]

        response = dynra_repl(code="1 / 0")
        assert not response["success"]
        assert "ZeroDivisionError" in response["stdout"] + response["stderr"]
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

        assert not response["success"]
        assert "Invalid port" in response["error"]
