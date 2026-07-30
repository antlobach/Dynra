import sys
import builtins
import os
import socket
import threading
import io
import json
import contextlib
import re
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
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            while True:
                conn, addr = s.accept()
                with conn:
                    try:
                        # Read ALL data from the connection
                        chunks = []
                        while True:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            chunks.append(chunk)

                        full_data = b"".join(chunks).decode("utf-8")
                        if not full_data:
                            continue

                        # Detect in_md header
                        lines = full_data.splitlines(keepends=True)
                        has_header = (
                            lines
                            and lines[0].startswith("in_md('")
                            and "')" in lines[0]
                        )

                        data_to_run = "".join(lines[1:]) if has_header else full_data

                        if has_header and not data_to_run.strip():
                            # Header only, switch and return success
                            stdout, stderr = io.StringIO(), io.StringIO()
                            with stdout_redirector(stdout), stderr_redirector(stderr):
                                match = re.search(r"in_md\('([^']+)'\)", lines[0])
                                if match:
                                    self.manager.in_md(match.group(1))
                            response = {
                                "stdout": stdout.getvalue(),
                                "stderr": stderr.getvalue(),
                                "success": True,
                                "result": None,
                            }
                        else:
                            stdout, stderr = io.StringIO(), io.StringIO()
                            with stdout_redirector(stdout), stderr_redirector(stderr):
                                if has_header:
                                    match = re.search(r"in_md\('([^']+)'\)", lines[0])
                                    if match:
                                        self.manager.in_md(match.group(1))

                                self.manager.pre_execute()
                                ns = self.shell.user_ns

                                try:
                                    res = self.shell.run_cell(
                                        data_to_run, store_history=False, silent=False
                                    )
                                    success = res.success
                                    result_value = (
                                        repr(res.result)
                                        if res.result is not None
                                        else None
                                    )
                                except Exception as e:
                                    success = False
                                    result_value = str(e)
                                    print(f"Error: {e}")

                            response = {
                                "stdout": stdout.getvalue(),
                                "stderr": stderr.getvalue(),
                                "success": success,
                                "result": result_value,
                            }

                        conn.sendall(json.dumps(response).encode("utf-8"))
                    except Exception as e:
                        print(f"Server Error: {e}")


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


import argparse


def main():
    parser = argparse.ArgumentParser(description="Dynra REPL Server")
    parser.add_argument(
        "--port", type=int, default=9999, help="Port to run the Dynra server on"
    )
    args = parser.parse_args()

    manager = NamespaceManager()
    builtins.md, builtins.in_md = manager.md, manager.in_md
    builtins.ls_md = manager.ls_md
    builtins.help_dynra = manager.help_dynra
    builtins.dir_md = manager.dir_md
    builtins.doc = manager.doc
    builtins.source = manager.source
    builtins.find_var = manager.find_var

    sys.path.insert(0, os.getcwd())

    app = TerminalIPythonApp.instance()
    app.initialize(argv=[])

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
