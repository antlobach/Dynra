import argparse
import json
import socket
import sys
from pathlib import Path


def send_code(code, host="127.0.0.1", port=9999, module=None, create=False):
    if module is not None:
        header = json.dumps(
            {"module": module, "create": create}, separators=(",", ":")
        )
        code = f"DYNRA {header}\n{code}"
    try:
        with socket.create_connection((host, port)) as connection:
            connection.sendall(code.encode("utf-8"))
            connection.shutdown(socket.SHUT_WR)
            response_data = bytearray()
            while chunk := connection.recv(4096):
                response_data.extend(chunk)
    except ConnectionRefusedError:
        print(
            f"Error: Could not connect to Dynra at {host}:{port}.",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    response = json.loads(response_data.decode("utf-8"))
    if response["stdout"]:
        print(response["stdout"], end="")
    if response["stderr"]:
        print(response["stderr"], file=sys.stderr, end="")
    if response["result"] and response["result"] != "None":
        print(f"Result: {response['result']}")
    return 0 if response["success"] else 1


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
