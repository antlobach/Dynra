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
    if "error" in response and response.get("error") and "success" not in response:
        return {
            "success": False,
            "module": module,
            "result": None,
            "stdout": "",
            "stderr": "",
            "error": response["error"],
        }

    success = bool(response.get("success"))
    return {
        "success": success,
        "module": module,
        "result": response.get("result"),
        "stdout": response.get("stdout", ""),
        "stderr": response.get("stderr", ""),
        "error": None
        if success
        else response.get("error") or "Python execution failed.",
    }


@mcp.tool()
def dynra_repl(code: str, module: str = "user", create: bool = False) -> dict:
    """Evaluate Python in a persistent live module.

    Pass the owning project module when inspecting or patching source. Imports
    are strict: failures are returned instead of creating empty namespaces.
    Set ``create=True`` only when intentionally creating a synthetic scratch
    module. State persists between calls.
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

    header = json.dumps(
        {"module": module, "create": create}, separators=(",", ":")
    )
    response = send_code(f"DYNRA {header}\n{code}")
    return _tool_response(response, module)


if __name__ == "__main__":
    mcp.run()
