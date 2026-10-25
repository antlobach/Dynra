import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from dynra.client import control_request, discover_runtimes
from dynra.registry import RuntimeRecord, load_records, register_runtime, unregister_runtime


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition was not met before timeout")


def test_registry_round_trip_and_malformed_pruning(tmp_path, monkeypatch):
    monkeypatch.setenv("DYNRA_RUNTIME_DIR", str(tmp_path))
    record = RuntimeRecord(
        runtime_id="runtime-one",
        pid=os.getpid(),
        host="127.0.0.1",
        port=9999,
        project_root=str(tmp_path),
        python_executable=sys.executable,
        dynra_version="test",
        protocol_version=1,
        started_at="2026-01-01T00:00:00+00:00",
    )

    path = register_runtime(record)
    assert load_records() == (record,)
    assert json.loads(path.read_text(encoding="utf-8"))["runtime_id"] == "runtime-one"

    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert load_records() == (record,)
    assert not (tmp_path / "broken.json").exists()

    unregister_runtime(record.runtime_id)
    assert load_records() == ()


def test_runtime_discovery_status_and_graceful_stop(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='sample'\nversion='1'\n")
    port = free_port()
    env = os.environ.copy()
    env["DYNRA_RUNTIME_DIR"] = str(registry)
    env["PYTHONUNBUFFERED"] = "1"
    monkeypatch.setenv("DYNRA_RUNTIME_DIR", str(registry))

    process = subprocess.Popen(
        [sys.executable, "-m", "dynra", "--port", str(port)],
        cwd=project,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        runtime = wait_for(lambda: discover_runtimes() and discover_runtimes()[0])
        assert runtime["port"] == port
        assert runtime["pid"] == process.pid
        assert runtime["project_root"] == str(project.resolve())
        assert runtime["protocol_version"] == 1
        assert "evaluate" in runtime["capabilities"]

        status = control_request(
            "status", port=port, runtime_id=runtime["runtime_id"]
        )
        assert status["success"] is True
        assert status["runtime"]["current_module"] == "user"

        mismatch = control_request("status", port=port, runtime_id="wrong")
        assert mismatch == {
            "success": False,
            "error": "Runtime identity mismatch.",
            "runtime_id": runtime["runtime_id"],
        }

        stopped = control_request(
            "stop", port=port, runtime_id=runtime["runtime_id"]
        )
        assert stopped["stopping"] is True
        assert process.wait(timeout=10) == 0
        wait_for(lambda: not tuple(registry.glob("*.json")))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_discovery_prunes_dead_process_record(tmp_path, monkeypatch):
    monkeypatch.setenv("DYNRA_RUNTIME_DIR", str(tmp_path))
    register_runtime(
        RuntimeRecord(
            runtime_id="dead",
            pid=999_999_999,
            host="127.0.0.1",
            port=1,
            project_root=str(tmp_path),
            python_executable=sys.executable,
            dynra_version="test",
            protocol_version=1,
            started_at="2026-01-01T00:00:00+00:00",
        )
    )

    assert discover_runtimes() == ()
    assert load_records() == ()
