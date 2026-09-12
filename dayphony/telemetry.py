from __future__ import annotations

import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class SystemTelemetry:
    cpu_load: float = 0.0
    memory_load: float = 0.0
    open_apps: int = 0
    communication_apps: int = 0
    creative_apps: int = 0
    coding_apps: int = 0


@dataclass(frozen=True)
class AiTelemetry:
    codex_tps: float = 0.0
    claude_tps: float = 0.0
    codex_change: float = 0.0
    claude_change: float = 0.0

    @property
    def total_tps(self) -> float:
        return self.codex_tps + self.claude_tps

    @property
    def activity(self) -> float:
        # Input contexts can be very large and arrive in bursts. Log scaling
        # gives the music useful movement without letting one request dominate.
        return _clamp(math.log1p(self.total_tps) / math.log1p(50_000.0))

    @property
    def movement(self) -> float:
        return max(abs(self.codex_change), abs(self.claude_change))


class SystemMonitor:
    def sample(self) -> SystemTelemetry:
        apps = self._running_applications()
        lowered = [app.lower() for app in apps]
        return SystemTelemetry(
            cpu_load=self._cpu_load(),
            memory_load=self._memory_load(),
            open_apps=len(apps),
            communication_apps=_count_matching(
                lowered, ("slack", "teams", "outlook", "mail", "messages", "zoom", "meet")
            ),
            creative_apps=_count_matching(
                lowered,
                (
                    "music",
                    "sonic pi",
                    "logic",
                    "ableton",
                    "reaper",
                    "figma",
                    "photoshop",
                    "preview",
                ),
            ),
            coding_apps=_count_matching(
                lowered,
                ("code", "cursor", "xcode", "terminal", "iterm", "warp", "claude", "chatgpt"),
            ),
        )

    def _cpu_load(self) -> float:
        try:
            result = subprocess.run(
                ["ps", "-A", "-o", "%cpu="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            total = sum(float(value) for value in result.stdout.split() if value)
            capacity = max(1, os.cpu_count() or 1) * 100.0
            return _clamp(total / capacity)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            try:
                return _clamp(os.getloadavg()[0] / max(1, os.cpu_count() or 1))
            except OSError:
                return 0.0

    def _memory_load(self) -> float:
        try:
            result = subprocess.run(
                ["memory_pressure"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            marker = "System-wide memory free percentage:"
            for line in result.stdout.splitlines():
                if marker in line:
                    free_percent = float(line.split(marker, 1)[1].strip().rstrip("%"))
                    return _clamp(1.0 - free_percent / 100.0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return 0.0

    def _running_applications(self) -> list[str]:
        script = (
            'ObjC.import("AppKit"); '
            "const apps=$.NSWorkspace.sharedWorkspace.runningApplications.js; "
            "apps.filter(app => Number(app.activationPolicy) === 0 && !Boolean(app.terminated))"
            '.map(app => app.localizedName.js).join("|")'
        )
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return [name for name in result.stdout.strip().split("|") if name]
        except (OSError, subprocess.TimeoutExpired):
            return []


class LocalTokenMonitor:
    """Reads only numeric token counters from recent local session records.

    Codex and Claude log formats are implementation details rather than stable
    public APIs. Every parser is defensive and a format change simply produces
    zero activity. Prompt, response, tool, and file content is never retained.
    """

    def __init__(
        self,
        codex_root: Path | None = None,
        claude_root: Path | None = None,
        window_seconds: float = 60.0,
    ) -> None:
        self.codex_root = Path(
            os.environ.get("DAYPHONY_CODEX_SESSIONS", codex_root or CODEX_SESSIONS)
        ).expanduser()
        self.claude_root = Path(
            os.environ.get("DAYPHONY_CLAUDE_PROJECTS", claude_root or CLAUDE_PROJECTS)
        ).expanduser()
        self.window_seconds = window_seconds
        self._files: dict[str, list[Path]] = {"codex": [], "claude": []}
        self._last_discovery = 0.0
        self._last_codex_tps: float | None = None
        self._last_claude_tps: float | None = None

    def sample(self, now: float | None = None) -> AiTelemetry:
        wall_time = time.time() if now is None else now
        if wall_time - self._last_discovery >= 20.0:
            self._files["codex"] = self._recent_files(self.codex_root)
            self._files["claude"] = self._recent_files(self.claude_root)
            self._last_discovery = wall_time
        codex_tps = self._codex_tps(self._files["codex"], wall_time)
        claude_tps = self._claude_tps(self._files["claude"], wall_time)
        sample = AiTelemetry(
            codex_tps=codex_tps,
            claude_tps=claude_tps,
            codex_change=_relative_change(codex_tps, self._last_codex_tps),
            claude_change=_relative_change(claude_tps, self._last_claude_tps),
        )
        self._last_codex_tps = codex_tps
        self._last_claude_tps = claude_tps
        return sample

    def _recent_files(self, root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        try:
            files = [path for path in root.rglob("*.jsonl") if path.is_file()]
        except OSError:
            return []
        files.sort(key=lambda path: _mtime(path), reverse=True)
        return files[:12]

    def _codex_tps(self, files: list[Path], now: float) -> float:
        cutoff = now - self.window_seconds
        records: dict[tuple[str, str], float] = {}
        for path in files:
            for item in _tail_json_objects(path):
                if item.get("type") != "token_usage_record":
                    continue
                timestamp = _timestamp(item.get("timestamp"))
                if timestamp is None or timestamp < cutoff or timestamp > now + 5:
                    continue
                payload = item.get("payload")
                usage = payload.get("usage") if isinstance(payload, dict) else None
                total = usage.get("total_tokens") if isinstance(usage, dict) else None
                if isinstance(total, (int, float)) and total >= 0:
                    records[(str(path), str(item.get("timestamp")))] = float(total)
        return sum(records.values()) / self.window_seconds

    def _claude_tps(self, files: list[Path], now: float) -> float:
        cutoff = now - self.window_seconds
        records: dict[tuple[str, str], float] = {}
        for path in files:
            for item in _tail_json_objects(path):
                if item.get("type") != "assistant":
                    continue
                timestamp = _timestamp(item.get("timestamp"))
                if timestamp is None or timestamp < cutoff or timestamp > now + 5:
                    continue
                message = item.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                total = sum(
                    float(usage.get(key, 0) or 0)
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                    if isinstance(usage.get(key, 0), (int, float))
                )
                message_id = message.get("id") if isinstance(message, dict) else None
                identity = str(message_id or item.get("uuid") or item.get("timestamp"))
                records[(str(path), identity)] = max(
                    total, records.get((str(path), identity), 0.0)
                )
        return sum(records.values()) / self.window_seconds


def _tail_json_objects(path: Path, byte_limit: int = 2_000_000) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            start = max(0, size - byte_limit)
            handle.seek(start)
            if start:
                handle.readline()
            data = handle.read()
    except OSError:
        return []

    objects: list[dict[str, Any]] = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _count_matching(apps: list[str], fragments: tuple[str, ...]) -> int:
    return sum(1 for app in apps if any(fragment in app for fragment in fragments))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _relative_change(current: float, previous: float | None) -> float:
    if previous is None:
        return 0.0
    # Compare against 15% of the previous rolling rate, with a small floor so
    # low-volume sessions still produce useful movement without reacting to
    # single-token noise.
    scale = max(5.0, previous * 0.15)
    return max(-1.0, min(1.0, (current - previous) / scale))
