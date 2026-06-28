from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

AIOS_DIR = Path.home() / ".aios"
PIDS_DIR = AIOS_DIR / "pids"
LOGS_DIR = AIOS_DIR / "logs"


def _ensure_dirs() -> None:
    PIDS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def pid_file(agent_name: str) -> Path:
    return PIDS_DIR / f"{agent_name}.json"


def log_file(agent_name: str) -> Path:
    return LOGS_DIR / f"{agent_name}.log"


def spawn(agent_file: Path, agent_name: str) -> int:
    _ensure_dirs()
    log = log_file(agent_name).open("a")
    proc = subprocess.Popen(
        [sys.executable, str(agent_file)],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    pid_file(agent_name).write_text(json.dumps({"pid": proc.pid, "file": str(agent_file), "name": agent_name}))
    return proc.pid


def stop(agent_name: str) -> bool:
    pf = pid_file(agent_name)
    if not pf.exists():
        return False
    info = json.loads(pf.read_text())
    try:
        os.kill(info["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    pf.unlink(missing_ok=True)
    return True


def is_running(name: str) -> bool:
    info = get_info(name)
    if not info:
        return False

    pid = info.get("pid")
    if not pid:
        return False

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False

    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return str(pid) in result.stdout
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_info(agent_name: str) -> dict[str, Any] | None:
    pf = pid_file(agent_name)
    if not pf.exists():
        return None
    return json.loads(pf.read_text())


def list_agents() -> list[dict[str, Any]]:
    _ensure_dirs()
    agents = []
    for pf in PIDS_DIR.glob("*.json"):
        info = json.loads(pf.read_text())
        info["running"] = is_running(info["name"])
        agents.append(info)
    return agents


class ProcessManager:
    """Namespace for process management utilities."""

    spawn = staticmethod(spawn)
    stop = staticmethod(stop)
    is_running = staticmethod(is_running)
    get_info = staticmethod(get_info)
    list_agents = staticmethod(list_agents)
    log_file = staticmethod(log_file)
    LOGS_DIR = LOGS_DIR
    AIOS_DIR = AIOS_DIR
