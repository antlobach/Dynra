import json
import re
import socket

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Dynra: live Python for coding agents")
_MODULE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def send_code(code: str, host="127.0.0.1", port=9999, timeout=30.0):
    """Send code to the trusted local Dynra runtime."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(code.encode("utf-8"))
            connection.shutdown(socket.SHUT_WR)
            response_data = bytearray()
            while chunk := connection.recv(4096):
                response_data.extend(chunk)
            return json.loads(response_data.decode("utf-8"))
    except ConnectionRefusedError:
        return {
            "error": (
                "Could not connect to the Dynra runtime at 127.0.0.1:9999. "
                "Start it with 'uv run dynra'."
            )
        }
    except TimeoutError:
        return {"error": f"Dynra did not respond within {timeout:g} seconds."}
    except Exception as error:
        return {"error": str(error)}


def _tool_response(response, module):
    if "error" in response:
        return {
            "success": False,
            "module": module,
            "result": None,
            "stdout": "",
            "stderr": "",
            "error": response["error"],
        }

    return {
        "success": bool(response.get("success")),
        "module": module,
        "result": response.get("result"),
        "stdout": response.get("stdout", ""),
        "stderr": response.get("stderr", ""),
        "error": None if response.get("success") else "Python execution failed.",
    }


@mcp.tool()
def dynra_repl(code: str, module: str = "user") -> dict:
    """Evaluate Python in a persistent live module.

    This is Dynra's complete interface for coding agents. Use it to inspect
    runtime state, reproduce behavior, redefine functions or classes, and
    verify changes without restarting Python. Always pass the owning module
    when editing project code. Send complete top-level definitions for live
    patches. State persists between calls.
    """
    if not _MODULE_NAME.fullmatch(module):
        return {
            "success": False,
            "module": module,
            "result": None,
            "stdout": "",
            "stderr": "",
            "error": f"Invalid Python module name: {module!r}",
        }
    if not code.strip():
        return {
            "success": False,
            "module": module,
            "result": None,
            "stdout": "",
            "stderr": "",
            "error": "Code must not be empty.",
        }

    response = send_code(f"in_md('{module}')\n{code}")
    return _tool_response(response, module)


if __name__ == "__main__":
    mcp.run()
