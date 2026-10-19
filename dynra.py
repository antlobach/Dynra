import argparse
import contextlib
import io
import json
import socket
import sys
import threading

from IPython.terminal.ipapp import TerminalIPythonApp
from IPython.terminal.prompts import Prompts, Token

from dynra_core import NamespaceManager


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
    def __init__(self, shell, manager, host="127.0.0.1", port=9999):
        super().__init__(daemon=True)
        self.shell = shell
        self.manager = manager
        self.host = host
        self.port = port

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()
            while True:
                connection, _ = server.accept()
                with connection:
                    chunks = []
                    while chunk := connection.recv(4096):
                        chunks.append(chunk)
                    payload = b"".join(chunks).decode("utf-8")
                    if not payload:
                        continue
                    response = self._evaluate_request(payload)
                    connection.sendall(json.dumps(response).encode("utf-8"))

    def _evaluate_request(self, payload):
        stdout, stderr = io.StringIO(), io.StringIO()
        success = False
        result_value = None
        error = None
        try:
            lines = payload.splitlines(keepends=True)
            request = None
            if lines and lines[0].startswith("DYNRA "):
                request = json.loads(lines[0][len("DYNRA ") :])
                code = "".join(lines[1:])
            else:
                code = payload
            with stdout_redirector(stdout), stderr_redirector(stderr):
                if request is not None:
                    module = request["module"]
                    if request.get("create"):
                        self.manager.create_md(module)
                    self.manager.in_md(module)
                if code.strip():
                    execution = self.manager.execute_code(code)
                    success = execution.success
                    result_value = (
                        repr(execution.result)
                        if execution.result is not None
                        else None
                    )
                    if execution.error is not None:
                        error = f"{type(execution.error).__name__}: {execution.error}"
                else:
                    success = True
        except Exception as exception:
            error = f"{type(exception).__name__}: {exception}"
            with stderr_redirector(stderr):
                print(error, file=sys.stderr)
        return {
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "success": success,
            "result": result_value,
            "error": error,
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



def main():
    parser = argparse.ArgumentParser(description="Dynra REPL Server")
    parser.add_argument(
        "--port", type=int, default=9999, help="Port to run the Dynra server on"
    )
    args = parser.parse_args()

    manager = NamespaceManager()

    app = TerminalIPythonApp.instance()
    app.initialize(
        argv=[
            "--TerminalInteractiveShell.colors=NoColor",
            "--TerminalInteractiveShell.simple_prompt=True",
        ]
    )

    shell = app.shell
    shell.colors = "NoColor"  # Disable ANSI colors
    shell.color_info = False
    from IPython.core.interactiveshell import InteractiveShell

    InteractiveShell.colors = "NoColor"

    manager.shell = shell

    # Enable Autoreload
    shell.extension_manager.load_extension("autoreload")
    shell.run_line_magic("autoreload", "2")

    # Enable Dynra Patching
    shell.events.register("pre_execute", manager.pre_execute)
    shell.events.register("post_execute", manager.post_execute)

    manager._init_ipython_ns(sys.modules["user"].__dict__)
    shell.user_ns = sys.modules["user"].__dict__
    shell.user_module = sys.modules["user"]
    shell.banner1 = (
        f"Welcome to Dynra - Clojure-like REPL with Live Patching (Port: {args.port})\n"
    )
    shell.prompts = DynraPrompts(shell, manager)

    # ALWAYS Start Remote Server immediately
    server = DynraServer(shell, manager, port=args.port)
    server.start()

    # Only enter interactive loop if we have a TTY
    if sys.stdin.isatty():
        app.start()
    else:
        print(f"Dynra Server live on port {args.port}.")
        # Keep the main thread alive for the server
        import time

        while True:
            time.sleep(1)


def run_cli():
    """Entry point for uv run dynra"""
    main()


if __name__ == "__main__":
    main()
