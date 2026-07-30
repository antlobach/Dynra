import pexpect
import subprocess
import sys

def test_cli_client_server():
    python_exec = sys.executable
    server = pexpect.spawn(python_exec, ["dynra.py"], encoding="utf-8")

    try:
        server.expect(r"user> ", timeout=10)

        def run_client(code):
            result = subprocess.run(
                [python_exec, "dynra_client.py", code],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        run_client('md("cli_test")')
        run_client('in_md("cli_test")')
        run_client("def multiply(a, b): return a * b")
        assert "50" in run_client("multiply(10, 5)")
    finally:
        server.terminate(force=True)
