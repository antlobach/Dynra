from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class RuntimeRecord:
    runtime_id: str
    pid: int
    host: str
    port: int
    project_root: str
    python_executable: str
    dynra_version: str
    protocol_version: int
    started_at: str

    @classmethod
    def from_dict(cls, value: dict) -> "RuntimeRecord":
        return cls(
            runtime_id=str(value["runtime_id"]),
            pid=int(value["pid"]),
            host=str(value["host"]),
            port=int(value["port"]),
            project_root=str(value["project_root"]),
            python_executable=str(value["python_executable"]),
            dynra_version=str(value["dynra_version"]),
            protocol_version=int(value["protocol_version"]),
            started_at=str(value["started_at"]),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def registry_directory() -> Path:
    override = os.environ.get("DYNRA_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return base / "dynra" / "runtimes"


def _record_path(runtime_id: str) -> Path:
    return registry_directory() / f"{runtime_id}.json"


def register_runtime(record: RuntimeRecord) -> Path:
    directory = registry_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = _record_path(record.runtime_id)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{record.runtime_id}.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record.to_dict(), stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def unregister_runtime(runtime_id: str) -> None:
    _record_path(runtime_id).unlink(missing_ok=True)


def load_records(*, prune_malformed: bool = True) -> tuple[RuntimeRecord, ...]:
    directory = registry_directory()
    if not directory.is_dir():
        return ()
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record = RuntimeRecord.from_dict(data)
            if path.stem != record.runtime_id:
                raise ValueError("runtime ID does not match registry filename")
            records.append(record)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            if prune_malformed:
                path.unlink(missing_ok=True)
    return tuple(records)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
