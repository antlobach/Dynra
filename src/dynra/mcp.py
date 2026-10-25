import re

from mcp.server.fastmcp import FastMCP

from .client import (
    RuntimeConnectionError,
    control_request,
    discover_runtimes,
    find_runtime,
    request_runtime,
)


mcp = FastMCP("Dynra: live Python for coding agents")
_MODULE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def _error_response(error):
    return {"success": False, "error": str(error)}


def _validate_port(port):
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return f"Invalid port: {port!r}; expected an integer from 1 to 65535."
    return None


def _runtime_endpoint(runtime, port):
    if runtime:
        selected = find_runtime(runtime)
        return selected["host"], selected["port"], selected["runtime_id"]
    error = _validate_port(port)
    if error:
        raise ValueError(error)
    return "127.0.0.1", port, None


def _control(operation, *, runtime="", port=9999, **arguments):
    try:
        host, selected_port, runtime_id = _runtime_endpoint(runtime, port)
        return control_request(
            operation,
            host=host,
            port=selected_port,
            runtime_id=runtime_id,
            timeout=30.0,
            **arguments,
        )
    except (RuntimeConnectionError, ValueError) as error:
        return _error_response(error)


def _compact_source_sync(source_sync, selected_module):
    if not source_sync:
        return None
    status = source_sync.get("status", "clean")
    conflicts = source_sync.get("conflicts") or ()
    live = source_sync.get("live") or ()
    changes = source_sync.get("changes") or ()
    outcomes = source_sync.get("outcomes") or ()
    restart_required = source_sync.get("restart_required") or ()
    error = source_sync.get("error")
    if (
        status == "clean"
        and not conflicts
        and not live
        and not changes
        and not outcomes
        and not restart_required
        and not error
    ):
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
    if outcomes:
        compact["outcomes"] = outcomes
    if restart_required:
        compact["restart_required"] = restart_required
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
    if response.get("lifecycle_plan") is not None:
        compact["lifecycle_plan"] = response["lifecycle_plan"]
    if not success:
        compact["error"] = response.get("error") or "Python execution failed."
    return compact


@mcp.tool()
def dynra_runtimes() -> dict:
    """List live local Dynra runtimes registered by project and runtime ID."""
    return {"success": True, "runtimes": list(discover_runtimes())}


@mcp.tool()
def dynra_status(runtime: str = "", port: int = 9999) -> dict:
    """Return identity, project, generation, and source state for a runtime."""
    return _control("status", runtime=runtime, port=port)


@mcp.tool()
def dynra_modules(runtime: str = "", port: int = 9999) -> dict:
    """List discovered, loaded, synthetic, and external modules."""
    return _control("modules", runtime=runtime, port=port)


@mcp.tool()
def dynra_symbols(module: str, runtime: str = "", port: int = 9999) -> dict:
    """List public symbols without importing an unloaded project module."""
    if not _MODULE_NAME.fullmatch(module):
        return _error_response(f"Invalid Python module name: {module!r}")
    return _control("symbols", runtime=runtime, port=port, module=module)


@mcp.tool()
def dynra_source(target: str, runtime: str = "", port: int = 9999) -> dict:
    """Return source for a qualified project function or class."""
    if not target.strip():
        return _error_response("Target must not be empty.")
    return _control("source", runtime=runtime, port=port, target=target)


@mcp.tool()
def dynra_diff(runtime: str = "", port: int = 9999) -> dict:
    """Return live definitions that differ from project source files."""
    return _control("diff", runtime=runtime, port=port)


@mcp.tool()
def dynra_persist(target: str = "", runtime: str = "", port: int = 9999) -> dict:
    """Persist one qualified live definition, or every live definition."""
    return _control("persist", runtime=runtime, port=port, target=target or None)


@mcp.tool()
def dynra_revert(target: str, runtime: str = "", port: int = 9999) -> dict:
    """Revert one qualified live definition to its previous snapshot."""
    if not target.strip():
        return _error_response("Target must not be empty.")
    return _control("revert", runtime=runtime, port=port, target=target)


@mcp.tool()
def dynra_patch_log(runtime: str = "", port: int = 9999) -> dict:
    """Return the runtime's structured patch and persistence history."""
    return _control("patch_log", runtime=runtime, port=port)


@mcp.tool()
def dynra_events(
    after: int = 0,
    limit: int | None = None,
    runtime: str = "",
    port: int = 9999,
) -> dict:
    """Read append-only runtime events after a sequence cursor."""
    return _control(
        "events",
        runtime=runtime,
        port=port,
        after=after,
        limit=limit,
    )


@mcp.tool()
def dynra_lifecycle_plan(
    definitions: list[str],
    runtime: str = "",
    port: int = 9999,
) -> dict:
    """Plan lifecycle impact without mutating the runtime."""
    return _control(
        "lifecycle_plan",
        runtime=runtime,
        port=port,
        definitions=definitions,
    )


@mcp.tool()
def dynra_bootstrap(force: bool = False, runtime: str = "", port: int = 9999) -> dict:
    """Explicitly run the project's configured bootstrap target."""
    return _control("bootstrap", runtime=runtime, port=port, force=force)


@mcp.tool()
def dynra_lifecycle_reconcile(
    definitions: list[str],
    source_before: dict[str, str] | None = None,
    source_after: dict[str, str] | None = None,
    runtime: str = "",
    port: int = 9999,
) -> dict:
    """Execute an adapter-managed transactional lifecycle reconciliation."""
    return _control(
        "lifecycle_reconcile",
        runtime=runtime,
        port=port,
        definitions=definitions,
        source_before=source_before or {},
        source_after=source_after or {},
    )


@mcp.tool()
def dynra_bootstrap_status(runtime: str = "", port: int = 9999) -> dict:
    """Return configured bootstrap state without starting the application."""
    return _control("bootstrap_status", runtime=runtime, port=port)


@mcp.tool()
def dynra_lifecycle_journal(runtime: str = "", port: int = 9999) -> dict:
    """Read lifecycle plans, swap points, outcomes, and recovery state."""
    return _control("lifecycle_journal", runtime=runtime, port=port)


@mcp.tool()
def dynra_evaluate(
    code: str,
    module: str = "user",
    create: bool = False,
    runtime: str = "",
    port: int = 9999,
) -> dict:
    """Evaluate Python in a persistent live module."""
    return _evaluate(code, module, create, runtime, port)


def _evaluate(code, module, create, runtime, port):
    if not _MODULE_NAME.fullmatch(module):
        return _error_response(f"Invalid Python module name: {module!r}")
    if not code.strip():
        return _error_response("Code must not be empty.")
    try:
        host, selected_port, _ = _runtime_endpoint(runtime, port)
        response = request_runtime(
            {"module": module, "create": create},
            host=host,
            port=selected_port,
            code=code,
            timeout=30.0,
        )
    except (RuntimeConnectionError, ValueError) as error:
        return _error_response(error)
    return _tool_response(response, module)


@mcp.tool()
def dynra_repl(
    code: str, module: str = "user", create: bool = False, port: int = 9999
) -> dict:
    """Evaluate Python in a persistent runtime selected by port.

    This compatibility tool preserves the original API. New integrations can
    use ``dynra_evaluate`` with a discovered runtime ID.
    """
    return _evaluate(code, module, create, "", port)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
