from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any


AGENTS = ("codex", "claude", "other")
ATTENTION_REASONS = ("input_needed", "approval_needed", "blocked", "error")
DEFAULT_SOCKET = Path.home() / "Library" / "Caches" / "Dayphony" / "control.sock"
MAX_REQUEST_BYTES = 16_384


class ControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentEvent:
    agent: str
    reason: str
    priority: float


def socket_path() -> Path:
    return Path(os.environ.get("DAYPHONY_SOCKET", DEFAULT_SOCKET)).expanduser()


class ControlServer:
    """Private local IPC endpoint used by CLI and MCP adapter processes."""

    def __init__(self, events: Queue[AgentEvent], path: Path | None = None) -> None:
        self.events = events
        self.path = (path or socket_path()).resolve()
        self._status: dict[str, Any] = {"running": True, "state": "starting"}
        self._status_lock = threading.Lock()
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self._remove_stale_socket()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.path))
            os.chmod(self.path, 0o600)
            server.listen(8)
            server.settimeout(0.5)
        except Exception:
            server.close()
            raise

        self._socket = server
        self._thread = threading.Thread(
            target=self._serve,
            name="dayphony-control",
            daemon=True,
        )
        self._thread.start()

    def update_status(self, status: dict[str, Any]) -> None:
        with self._status_lock:
            self._status = {"running": True, **status}

    def close(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._unlink_own_socket()

    def _remove_stale_socket(self) -> None:
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise ControlError(f"Refusing to replace non-socket path: {self.path}")
        try:
            response = send_control_request({"method": "status"}, self.path, timeout=0.3)
        except ControlError:
            self.path.unlink(missing_ok=True)
            return
        if response.get("ok"):
            raise ControlError("Another Dayphony control server is already running")
        self.path.unlink(missing_ok=True)

    def _unlink_own_socket(self) -> None:
        try:
            if stat.S_ISSOCK(self.path.lstat().st_mode):
                self.path.unlink()
        except FileNotFoundError:
            pass

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                response = self._read_and_handle(connection)
                try:
                    connection.sendall(
                        json.dumps(response, separators=(",", ":")).encode() + b"\n"
                    )
                except OSError:
                    pass

    def _read_and_handle(self, connection: socket.socket) -> dict[str, Any]:
        connection.settimeout(1.0)
        data = bytearray()
        try:
            while len(data) <= MAX_REQUEST_BYTES:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
                if b"\n" in chunk:
                    break
        except (OSError, socket.timeout):
            return {"ok": False, "error": "request read failed"}
        if len(data) > MAX_REQUEST_BYTES:
            return {"ok": False, "error": "request too large"}
        try:
            request = json.loads(bytes(data).split(b"\n", 1)[0])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"ok": False, "error": "invalid JSON"}
        if not isinstance(request, dict):
            return {"ok": False, "error": "request must be an object"}
        return self._handle(request)

    def _handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        if method == "status":
            with self._status_lock:
                return {"ok": True, **self._status}
        if method != "notify":
            return {"ok": False, "error": "unknown method"}

        agent = request.get("agent")
        reason = request.get("reason")
        priority = request.get("priority", 0.7)
        if agent not in AGENTS:
            return {"ok": False, "error": f"agent must be one of: {', '.join(AGENTS)}"}
        if reason not in ATTENTION_REASONS:
            return {
                "ok": False,
                "error": f"reason must be one of: {', '.join(ATTENTION_REASONS)}",
            }
        if not isinstance(priority, (int, float)) or isinstance(priority, bool):
            return {"ok": False, "error": "priority must be a number"}
        priority = max(0.0, min(1.0, float(priority)))
        self.events.put(AgentEvent(agent=agent, reason=reason, priority=priority))
        return {
            "ok": True,
            "accepted": {"agent": agent, "reason": reason, "priority": priority},
        }


def send_control_request(
    request: dict[str, Any],
    path: Path | None = None,
    timeout: float = 1.0,
) -> dict[str, Any]:
    endpoint = (path or socket_path()).resolve()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(endpoint))
        client.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        data = bytearray()
        while len(data) <= MAX_REQUEST_BYTES:
            chunk = client.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as error:
        raise ControlError(f"Dayphony is not reachable at {endpoint}: {error}") from error
    finally:
        client.close()
    try:
        response = json.loads(bytes(data).split(b"\n", 1)[0])
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ControlError("Dayphony returned an invalid response") from error
    if not isinstance(response, dict):
        raise ControlError("Dayphony returned an invalid response")
    return response


def signal_main() -> int:
    parser = argparse.ArgumentParser(description="Send a local agent signal to Dayphony")
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--reason", choices=ATTENTION_REASONS, default="input_needed")
    parser.add_argument("--priority", type=float, default=0.7)
    args = parser.parse_args()
    try:
        response = send_control_request(
            {
                "method": "notify",
                "agent": args.agent,
                "reason": args.reason,
                "priority": args.priority,
            }
        )
    except ControlError as error:
        print(error)
        return 1
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0 if response.get("ok") else 1
