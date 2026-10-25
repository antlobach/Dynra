from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from .registry import (
    PROTOCOL_VERSION,
    RuntimeRecord,
    load_records,
    process_is_alive,
    unregister_runtime,
)


class RuntimeConnectionError(RuntimeError):
    pass


def request_runtime(
    request: dict,
    *,
    host: str = "127.0.0.1",
    port: int = 9999,
    code: str = "",
    timeout: float = 5.0,
) -> dict:
    header = json.dumps(request, separators=(",", ":"))
    payload = f"DYNRA {header}\n{code}".encode("utf-8")
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            response_data = bytearray()
            while chunk := connection.recv(4096):
                response_data.extend(chunk)
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError) as error:
        raise RuntimeConnectionError(
            f"Could not connect to Dynra at {host}:{port}: {error}"
        ) from error
    try:
        return json.loads(response_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeConnectionError(
            f"Invalid response from Dynra at {host}:{port}."
        ) from error


def control_request(
    operation: str,
    *,
    host: str = "127.0.0.1",
    port: int = 9999,
    runtime_id: str | None = None,
    timeout: float = 5.0,
    **arguments,
) -> dict:
    request = {
        "protocol": PROTOCOL_VERSION,
        "operation": operation,
        **arguments,
    }
    if runtime_id is not None:
        request["runtime_id"] = runtime_id
    return request_runtime(
        request, host=host, port=port, timeout=timeout
    )


def discover_runtimes() -> tuple[dict, ...]:
    runtimes = []
    for record in load_records():
        if not process_is_alive(record.pid):
            unregister_runtime(record.runtime_id)
            continue
        try:
            response = control_request(
                "handshake",
                host=record.host,
                port=record.port,
                runtime_id=record.runtime_id,
                timeout=0.5,
            )
        except RuntimeConnectionError:
            unregister_runtime(record.runtime_id)
            continue
        runtime = response.get("runtime") if response.get("success") else None
        if not runtime or runtime.get("runtime_id") != record.runtime_id:
            unregister_runtime(record.runtime_id)
            continue
        runtimes.append(runtime)
    return tuple(sorted(runtimes, key=lambda item: item["started_at"]))


def find_runtime(selector: str) -> dict:
    runtimes = discover_runtimes()
    exact = [runtime for runtime in runtimes if runtime["runtime_id"] == selector]
    if exact:
        return exact[0]
    try:
        port = int(selector)
    except ValueError:
        port = None
    by_port = [runtime for runtime in runtimes if runtime["port"] == port]
    if len(by_port) == 1:
        return by_port[0]
    project = [
        runtime
        for runtime in runtimes
        if runtime["project_root"] == str(Path(selector).expanduser().resolve())
    ]
    if len(project) == 1:
        return project[0]
    if not exact and not by_port and not project:
        raise RuntimeConnectionError(f"No running Dynra runtime matches {selector!r}.")
    raise RuntimeConnectionError(f"Runtime selector {selector!r} is ambiguous.")


def send_code(code, host="127.0.0.1", port=9999, module=None, create=False):
    request = {"module": module, "create": create} if module is not None else None
    try:
        if request is None:
            with socket.create_connection((host, port), timeout=5.0) as connection:
                connection.sendall(code.encode("utf-8"))
                connection.shutdown(socket.SHUT_WR)
                response_data = bytearray()
                while chunk := connection.recv(4096):
                    response_data.extend(chunk)
            response = json.loads(response_data.decode("utf-8"))
        else:
            response = request_runtime(request, host=host, port=port, code=code)
    except (RuntimeConnectionError, ConnectionRefusedError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if response.get("stdout"):
        print(response["stdout"], end="")
    if response.get("stderr"):
        print(response["stderr"], file=sys.stderr, end="")
    if response.get("result") and response["result"] != "None":
        print(f"Result: {response['result']}")
    return 0 if response.get("success") else 1


def main():
    parser = argparse.ArgumentParser(description="Send code to a running Dynra REPL.")
    parser.add_argument("code", nargs="?", help="Python code to execute")
    parser.add_argument("-f", "--file", help="File containing Python code to execute")
    parser.add_argument(
        "-p", "--port", type=int, default=9999, help="Port Dynra is listening on"
    )
    parser.add_argument("-m", "--module", help="Existing module to enter atomically")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create --module as a synthetic REPL namespace",
    )
    args = parser.parse_args()

    if args.create and not args.module:
        parser.error("--create requires --module")
    if args.file:
        code = Path(args.file).read_text(encoding="utf-8")
    elif args.code:
        code = args.code
    else:
        parser.print_help()
        return 1
    return send_code(code, port=args.port, module=args.module, create=args.create)


if __name__ == "__main__":
    raise SystemExit(main())
