from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from IPython.terminal.ipapp import TerminalIPythonApp
from IPython.terminal.prompts import Prompts, Token

from . import __version__
from .core import NamespaceManager
from .registry import (
    PROTOCOL_VERSION,
    RuntimeRecord,
    register_runtime,
    unregister_runtime,
)


_CAPABILITIES = (
    "evaluate",
    "handshake",
    "status",
    "stop",
    "source-healing",
    "persist-source",
    "class-patching",
)


class DynraPrompts(Prompts):
    def __init__(self, shell, manager):
        super().__init__(shell)
        self.manager = manager

    def in_prompt_tokens(self, cli=None):
        return [
            (Token.Prompt, self.manager.current_name),
            (Token.Prompt, "> "),
        ]


class DynraServer(threading.Thread):
    def __init__(
        self,
        shell,
        manager,
        host="127.0.0.1",
        port=9999,
        runtime_id=None,
    ):
        super().__init__(daemon=True)
        self.shell = shell
        self.manager = manager
        self.host = host
        self.port = port
        self.runtime_id = runtime_id or uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.startup_error = None
        self.record = None

    def runtime_status(self):
        source_sync = self.manager.source_sync_status()
        return {
            "runtime_id": self.runtime_id,
            "pid": os.getpid(),
            "host": self.host,
            "port": self.port,
            "project_root": str(self.manager.project.root),
            "python_executable": sys.executable,
            "dynra_version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "started_at": self.started_at,
            "current_module": self.manager.current_name,
            "generation": self.manager.module_generation(),
            "source_sync": source_sync,
            "capabilities": list(_CAPABILITIES),
        }

    def run(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                self.port = server.getsockname()[1]
                server.listen()
                server.settimeout(0.2)
                status = self.runtime_status()
                self.record = RuntimeRecord(
                    runtime_id=self.runtime_id,
                    pid=status["pid"],
                    host=self.host,
                    port=self.port,
                    project_root=status["project_root"],
                    python_executable=status["python_executable"],
                    dynra_version=status["dynra_version"],
                    protocol_version=PROTOCOL_VERSION,
                    started_at=self.started_at,
                )
                register_runtime(self.record)
                self.ready.set()
                while not self.stopped.is_set():
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        chunks = []
                        while chunk := connection.recv(4096):
                            chunks.append(chunk)
                        payload = b"".join(chunks).decode("utf-8")
                        if not payload:
                            continue
                        response = self._evaluate_request(payload)
                        connection.sendall(json.dumps(response).encode("utf-8"))
                if hasattr(self.shell, "ask_exit"):
                    self.shell.ask_exit()
        except Exception as error:
            self.startup_error = error
            self.ready.set()
        finally:
            unregister_runtime(self.runtime_id)
            self.stopped.set()

    def stop(self):
        self.stopped.set()

    def _control_request(self, request):
        if request.get("protocol") != PROTOCOL_VERSION:
            return {
                "success": False,
                "error": "Unsupported Dynra protocol version.",
                "protocol": PROTOCOL_VERSION,
            }
        requested_runtime = request.get("runtime_id")
        if requested_runtime and requested_runtime != self.runtime_id:
            return {
                "success": False,
                "error": "Runtime identity mismatch.",
                "runtime_id": self.runtime_id,
            }
        operation = request.get("operation")
        if operation in {"handshake", "status"}:
            return {
                "success": True,
                "protocol": PROTOCOL_VERSION,
                "runtime": self.runtime_status(),
            }
        if operation == "stop":
            self.stop()
            return {
                "success": True,
                "protocol": PROTOCOL_VERSION,
                "runtime_id": self.runtime_id,
                "stopping": True,
            }
        return {
            "success": False,
            "error": f"Unknown Dynra operation: {operation!r}.",
            "protocol": PROTOCOL_VERSION,
        }

    def _evaluate_request(self, payload):
        stdout, stderr = io.StringIO(), io.StringIO()
        success = False
        result_value = None
        error = None
        source_sync = self.manager.source_sync_status()
        try:
            lines = payload.splitlines(keepends=True)
            request = None
            if lines and lines[0].startswith("DYNRA "):
                request = json.loads(lines[0][len("DYNRA ") :])
                code = "".join(lines[1:])
                if request.get("operation"):
                    return self._control_request(request)
            else:
                code = payload
            with stdout_redirector(stdout), stderr_redirector(stderr):
                if request is not None:
                    module = request["module"]
                    if request.get("create"):
                        self.manager.create_md(module)
                    self.manager.in_md(module, announce=False)
                    source_sync = self.manager.last_source_sync
                else:
                    source_sync = self.manager.reconcile_module_source()
                if code.strip():
                    execution = self.manager.execute_code(code, silent=request is not None)
                    success = execution.success
                    result_value = (
                        repr(execution.result)
                        if execution.result is not None
                        else None
                    )
                    if execution.error is not None:
                        error = f"{type(execution.error).__name__}: {execution.error}"
                    source_sync = self.manager.last_source_sync
                else:
                    success = True
        except Exception as exception:
            error = f"{type(exception).__name__}: {exception}"
            with stderr_redirector(stderr):
                print(error, file=sys.stderr)
        latest_sync = self.manager.source_sync_status()
        if source_sync.get("changes"):
            latest_sync["changes"] = source_sync["changes"]
            if latest_sync["status"] == "clean":
                latest_sync["status"] = "healed"
        if source_sync.get("error"):
            latest_sync["status"] = "error"
            latest_sync["error"] = source_sync["error"]
        return {
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "success": success,
            "result": result_value,
            "error": error,
            "generation": self.manager.module_generation(),
            "source_sync": latest_sync,
        }


@contextlib.contextmanager
def stdout_redirector(stream):
    old_stdout = sys.stdout
    sys.stdout = stream
    try:
        yield
    finally:
        sys.stdout = old_stdout


@contextlib.contextmanager
def stderr_redirector(stream):
    old_stderr = sys.stderr
    sys.stderr = stream
    try:
        yield
    finally:
        sys.stderr = old_stderr


def _print_runtime(runtime):
    print(
        f"{runtime['runtime_id']}  {runtime['project_root']}  "
        f"{runtime['host']}:{runtime['port']}  pid={runtime['pid']}"
    )


def _run_lifecycle_command(command, selector):
    from .client import RuntimeConnectionError, control_request, discover_runtimes, find_runtime

    try:
        if command in {"list", "clean"}:
            runtimes = discover_runtimes()
            for runtime in runtimes:
                _print_runtime(runtime)
            if command == "clean":
                print(f"Registry contains {len(runtimes)} live runtime(s).")
            return 0
        if selector is None:
            runtimes = discover_runtimes()
            if command == "status":
                for runtime in runtimes:
                    print(json.dumps(runtime, sort_keys=True))
                return 0
            raise RuntimeConnectionError(f"dynra {command} requires a runtime selector.")
        runtime = find_runtime(selector)
        if command == "status":
            response = control_request(
                "status",
                host=runtime["host"],
                port=runtime["port"],
                runtime_id=runtime["runtime_id"],
            )
            print(json.dumps(response["runtime"], sort_keys=True))
            return 0
        response = control_request(
            "stop",
            host=runtime["host"],
            port=runtime["port"],
            runtime_id=runtime["runtime_id"],
        )
        print(f"Stopping {response['runtime_id']}.")
        return 0
    except RuntimeConnectionError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Dynra live Python runtime")
    parser.add_argument("command", nargs="?", choices=("list", "status", "stop", "clean"))
    parser.add_argument("selector", nargs="?", help="Runtime ID, port, or project path")
    parser.add_argument(
        "--port", type=int, default=9999, help="Port to run the Dynra server on"
    )
    args = parser.parse_args()
    if args.command:
        return _run_lifecycle_command(args.command, args.selector)
    if args.selector:
        parser.error("unexpected runtime selector")

    manager = NamespaceManager()
    app = TerminalIPythonApp.instance()
    app.initialize(
        argv=[
            "--TerminalInteractiveShell.colors=NoColor",
            "--TerminalInteractiveShell.simple_prompt=True",
        ]
    )
    shell = app.shell
    shell.colors = "NoColor"
    shell.color_info = False
    from IPython.core.interactiveshell import InteractiveShell

    InteractiveShell.colors = "NoColor"
    manager.shell = shell
    shell.events.register("pre_execute", manager.pre_execute)
    shell.events.register("post_execute", manager.post_execute)
    manager._init_ipython_ns(sys.modules["user"].__dict__)
    shell.user_ns = sys.modules["user"].__dict__
    shell.user_module = sys.modules["user"]

    server = DynraServer(shell, manager, port=args.port)
    server.start()
    server.ready.wait()
    if server.startup_error is not None:
        raise server.startup_error
    shell.banner1 = (
        "Welcome to Dynra\n"
        f"Runtime: {server.runtime_id}\n"
        f"Project: {manager.project.root}\n"
        f"Python: {sys.executable}\n"
        f"Endpoint: {server.host}:{server.port}\n"
    )
    shell.prompts = DynraPrompts(shell, manager)

    try:
        if sys.stdin.isatty():
            app.start()
        else:
            print(f"Dynra runtime: {server.runtime_id}")
            print(f"Project: {manager.project.root}")
            print(f"Python: {sys.executable}")
            print(f"Endpoint: {server.host}:{server.port}", flush=True)
            server.stopped.wait()
    finally:
        server.stop()
        server.join(timeout=2)
    return 0


def run_cli():
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
