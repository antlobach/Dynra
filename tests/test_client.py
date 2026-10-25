import pexpect
import socket
import subprocess
import sys


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]

def test_cli_client_server():
    python_exec = sys.executable
    port = free_port()
    server = pexpect.spawn(
        python_exec, ["-m", "dynra", "--port", str(port)], encoding="utf-8"
    )

    try:
        server.expect(r"user> ", timeout=10)

        def run_client(code):
            result = subprocess.run(
                [python_exec, "-m", "dynra.client", "--port", str(port), code],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        run_client('create_md("cli_test")')
        run_client('in_md("cli_test")')
        run_client("def multiply(a, b): return a * b")
        assert "50" in run_client("multiply(10, 5)")
    finally:
        server.terminate(force=True)
