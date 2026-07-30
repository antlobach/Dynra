#!/usr/bin/env bash
set -euo pipefail

PORT=9999

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port|-p)
      if [[ $# -lt 2 ]]; then
        echo "Error: $1 requires a port." >&2
        exit 2
      fi
      PORT="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--port PORT]" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Error: port must be an integer between 1 and 65535. Got: $PORT" >&2
  exit 2
fi

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Error: port $PORT is already in use. Dynra will not terminate that process." >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run dynra --port "$PORT"
elif [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python dynra.py --port "$PORT"
else
  echo "Error: install uv or run 'uv sync' before starting Dynra." >&2
  exit 1
fi
