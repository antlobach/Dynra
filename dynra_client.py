import socket
import sys
import json
import argparse

def send_code(code, host='127.0.0.1', port=9999):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
            s.sendall(code.encode('utf-8'))
            s.shutdown(socket.SHUT_WR)
            response_data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk: break
                response_data += chunk
            
            response = json.loads(response_data.decode('utf-8'))
            
            if response['stdout']:
                print(response['stdout'], end='')
            if response['stderr']:
                print(response['stderr'], file=sys.stderr, end='')
            
            if response['result'] and response['result'] != 'None':
                print(f"Result: {response['result']}")
                
            if not response['success']:
                sys.exit(1)
    except ConnectionRefusedError:
        print("Error: Could not connect to dynra REPL. Is it running on port 9999?")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send code to a running dynra REPL.")
    parser.add_argument("code", nargs="?", help="Python code to execute")
    parser.add_argument("-f", "--file", help="File containing Python code to execute")
    parser.add_argument("-p", "--port", type=int, default=9999, help="Port dynra is listening on")
    
    args = parser.parse_args()
    
    if args.file:
        with open(args.file, "r") as f:
            code = f.read()
    elif args.code:
        code = args.code
    else:
        parser.print_help()
        sys.exit(1)
        
    send_code(code, port=args.port)
