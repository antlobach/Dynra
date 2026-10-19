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
                f"Could not connect to the Dynra runtime at {host}:{port}. "
                f"Start it with 'uv run dynra --port {port}'."
            )
        }
    except TimeoutError:
        return {"error": f"Dynra did not respond within {timeout:g} seconds."}
    except Exception as error:
        return {"error": str(error)}


def _error_response(error):
    return {"success": False, "error": error}


def _compact_source_sync(source_sync, selected_module):
    if not source_sync:
        return None
    status = source_sync.get("status", "clean")
    conflicts = source_sync.get("conflicts") or ()
    live = source_sync.get("live") or ()
    changes = source_sync.get("changes") or ()
    error = source_sync.get("error")
    if status == "clean" and not conflicts and not live and not changes and not error:
        return None

    compact = {"status": status}
    module = source_sync.get("module")
    if module and module != selected_module:
        compact["module"] = module
    generation = source_sync.get("generation")
    if generation:
        compact["generation"] = generation
    if conflicts:
        compact["conflicts"] = conflicts
    if live:
        compact["live"] = live
    if changes:
        compact["changes"] = changes
    if error:
        compact["error"] = error
    return compact


def _tool_response(response, module):
    if "error" in response and response.get("error") and "success" not in response:
        return _error_response(response["error"])

    success = bool(response.get("success"))
    compact = {"success": success}
    if response.get("result") is not None:
        compact["result"] = response["result"]
    for field in ("stdout", "stderr"):
        value = response.get(field, "").rstrip()
        if value:
            compact[field] = value
    source_sync = _compact_source_sync(response.get("source_sync"), module)
    if source_sync:
        compact["source_sync"] = source_sync
    if not success:
        compact["error"] = response.get("error") or "Python execution failed."
    return compact


@mcp.tool()
def dynra_repl(
    code: str, module: str = "user", create: bool = False, port: int = 9999
) -> dict:
    """Evaluate Python in a persistent live module.

    Pass the owning project module when inspecting or patching source. Imports
    are strict: failures are returned instead of creating empty namespaces.
    Set ``create=True`` only when intentionally creating a synthetic scratch
    module. Select the matching long-running runtime with ``port``. State
    persists between calls to the same runtime. Responses omit empty output,
    repeated request metadata, and clean source state.
    """
    if not _MODULE_NAME.fullmatch(module):
        return _error_response(f"Invalid Python module name: {module!r}")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return _error_response(
            f"Invalid port: {port!r}; expected an integer from 1 to 65535."
        )
    if not code.strip():
        return _error_response("Code must not be empty.")

    header = json.dumps(
        {"module": module, "create": create}, separators=(",", ":")
    )
    response = send_code(f"DYNRA {header}\n{code}", port=port)
    return _tool_response(response, module)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
