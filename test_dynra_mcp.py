import sys

import pexpect

from dynra_mcp import dynra_repl


def test_single_tool_agent_workflow():
    server = pexpect.spawn(sys.executable, ["dynra.py"], encoding="utf-8")

    try:
        server.expect(r"user> ", timeout=10)

        response = dynra_repl(code="1 + 1")
        assert response["success"]
        assert response["module"] == "user"
        assert response["result"] == "2"

        response = dynra_repl(
            module="agent_lib",
            code="def get_value(): return 1",
        )
        assert response["success"]

        response = dynra_repl(
            module="agent_app",
            code="from agent_lib import get_value",
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
