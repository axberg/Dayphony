from __future__ import annotations

import math
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

from .telemetry import AiTelemetry, LocalTokenMonitor, SystemMonitor, SystemTelemetry

SCENES = ("focus", "flow", "pressure", "recovery")


@dataclass(frozen=True)
class DayState:
    scene: str = "focus"
    focus: float = 0.8
    energy: float = 0.4
    urgency: float = 0.2
    social_load: float = 0.1
    cpu_load: float = 0.0
    memory_load: float = 0.0
    app_switch_rate: float = 0.0
    ai_activity: float = 0.0
    ai_change: float = 0.0
    codex_change: float = 0.0
    claude_change: float = 0.0
    codex_tps: float = 0.0
    claude_tps: float = 0.0
    open_apps: int = 0
    git_changes: int = 0
    active_app: str = "Unknown"
    event_serial: int = 0
    event_kind: str = "none"
    event_strength: float = 0.0
    meeting_active: bool = False
    source: str = "default"

    def clamped(self) -> "DayState":
        return replace(
            self,
            focus=_clamp(self.focus),
            energy=_clamp(self.energy),
            urgency=_clamp(self.urgency),
            social_load=_clamp(self.social_load),
            cpu_load=_clamp(self.cpu_load),
            memory_load=_clamp(self.memory_load),
            app_switch_rate=_clamp(self.app_switch_rate),
            ai_activity=_clamp(self.ai_activity),
            ai_change=_signed_clamp(self.ai_change),
            codex_change=_signed_clamp(self.codex_change),
            claude_change=_signed_clamp(self.claude_change),
            event_strength=_clamp(self.event_strength),
            open_apps=max(0, self.open_apps),
            git_changes=max(0, self.git_changes),
        )

    def smooth_towards(self, target: "DayState", dt: float, tau: float) -> "DayState":
        amount = 1.0 if tau <= 0 else 1.0 - math.exp(-max(0.0, dt) / tau)
        context_tau = min(tau, 6.0)
        context_amount = 1.0 if context_tau <= 0 else 1.0 - math.exp(-max(0.0, dt) / context_tau)
        return DayState(
            scene=target.scene,
            focus=_mix(self.focus, target.focus, amount),
            energy=_mix(self.energy, target.energy, amount),
            urgency=_mix(self.urgency, target.urgency, amount),
            social_load=_mix(self.social_load, target.social_load, amount),
            cpu_load=_mix(self.cpu_load, target.cpu_load, context_amount),
            memory_load=_mix(self.memory_load, target.memory_load, context_amount),
            app_switch_rate=_mix(self.app_switch_rate, target.app_switch_rate, context_amount),
            ai_activity=_mix(self.ai_activity, target.ai_activity, context_amount),
            ai_change=target.ai_change,
            codex_change=target.codex_change,
            claude_change=target.claude_change,
            codex_tps=target.codex_tps,
            claude_tps=target.claude_tps,
            open_apps=target.open_apps,
            git_changes=target.git_changes,
            active_app=target.active_app,
            event_serial=target.event_serial,
            event_kind=target.event_kind,
            event_strength=target.event_strength,
            meeting_active=target.meeting_active,
            source=target.source,
        ).clamped()


PRESETS: dict[str, DayState] = {
    "focus": DayState(
        "focus", focus=0.92, energy=0.38, urgency=0.12, social_load=0.05, source="preset"
    ),
    "flow": DayState(
        "flow", focus=0.86, energy=0.68, urgency=0.26, social_load=0.08, source="preset"
    ),
    "pressure": DayState(
        "pressure", focus=0.58, energy=0.92, urgency=0.88, social_load=0.35, source="preset"
    ),
    "recovery": DayState(
        "recovery", focus=0.45, energy=0.22, urgency=0.04, social_load=0.04, source="preset"
    ),
}


@dataclass(frozen=True)
class CalendarSignal:
    events_next_four_hours: int = 0
    minutes_to_next: float | None = None
    active_events: int = 0


class ContextCollector:
    def __init__(
        self,
        workspace: Path,
        include_calendar: bool = False,
        include_ai_telemetry: bool = False,
        system_monitor: SystemMonitor | None = None,
        token_monitor: LocalTokenMonitor | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.include_calendar = include_calendar
        self.system_monitor = system_monitor or SystemMonitor()
        self.token_monitor = token_monitor or (
            LocalTokenMonitor() if include_ai_telemetry else None
        )
        self.last_app = "Unknown"
        self.last_calendar_error: str | None = None
        self._last_git_changes: int | None = None
        self._last_cpu_load = 0.0
        self._last_ai_event_at = float("-inf")
        self._switches: deque[float] = deque()
        self._event_serial = 0
        self._sampled_once = False

    def sample(self) -> DayState:
        now = time.monotonic()
        app = self._frontmost_application()
        previous_app = self.last_app
        self.last_app = app
        app_key = app.lower()

        if any(
            name in app_key
            for name in (
                "code",
                "cursor",
                "xcode",
                "terminal",
                "iterm",
                "warp",
                "claude",
                "chatgpt",
            )
        ):
            focus, energy, urgency, social = 0.88, 0.56, 0.22, 0.05
        elif any(name in app_key for name in ("teams", "slack", "mail", "outlook", "zoom")):
            focus, energy, urgency, social = 0.36, 0.58, 0.52, 0.86
        elif any(name in app_key for name in ("keynote", "powerpoint", "meet")):
            focus, energy, urgency, social = 0.52, 0.62, 0.48, 0.66
        elif any(name in app_key for name in ("safari", "chrome", "firefox", "edge")):
            focus, energy, urgency, social = 0.62, 0.48, 0.30, 0.15
        else:
            focus, energy, urgency, social = 0.68, 0.45, 0.22, 0.10

        system = self.system_monitor.sample()
        ai = self.token_monitor.sample() if self.token_monitor else AiTelemetry()
        changed_files = self._changed_files()
        workload = min(changed_files / 12.0, 1.0)
        energy += workload * 0.12
        urgency += workload * 0.16

        if app != previous_app and previous_app != "Unknown":
            self._switches.append(now)
        while self._switches and self._switches[0] < now - 300.0:
            self._switches.popleft()
        switches_per_minute = len(self._switches) / 5.0
        switch_rate = _clamp(switches_per_minute / 3.0)

        open_app_load = _clamp((system.open_apps - 6) / 18.0)
        energy += system.cpu_load * 0.20 + ai.activity * 0.18
        urgency += system.cpu_load * 0.10 + open_app_load * 0.08
        social += min(system.communication_apps / 5.0, 1.0) * 0.13
        focus += min(system.coding_apps / 4.0, 1.0) * 0.06
        focus -= switch_rate * 0.22
        if system.memory_load > 0.72:
            pressure = (system.memory_load - 0.72) / 0.28
            focus -= pressure * 0.12
            urgency += pressure * 0.14

        calendar = self._calendar_signal() if self.include_calendar else CalendarSignal()
        meeting_active = calendar.active_events > 0 and any(
            name in app_key for name in ("teams", "zoom", "meet")
        )
        if calendar.events_next_four_hours:
            social += min(calendar.events_next_four_hours / 6.0, 1.0) * 0.24
        if calendar.minutes_to_next is not None and calendar.minutes_to_next < 25:
            nearness = 1.0 - max(calendar.minutes_to_next, 0.0) / 25.0
            urgency += nearness * 0.30
            focus -= nearness * 0.16

        event_kind, event_strength = self._event(
            app=app,
            previous_app=previous_app,
            changed_files=changed_files,
            ai=ai,
            system=system,
            now=now,
        )

        if urgency >= 0.70:
            scene = "pressure"
        elif focus >= 0.76 and energy >= 0.56:
            scene = "flow"
        elif focus >= 0.68:
            scene = "focus"
        else:
            scene = "recovery"

        return DayState(
            scene=scene,
            focus=focus,
            energy=energy,
            urgency=urgency,
            social_load=social,
            cpu_load=system.cpu_load,
            memory_load=system.memory_load,
            app_switch_rate=switch_rate,
            ai_activity=ai.activity,
            ai_change=(
                ai.codex_change
                if abs(ai.codex_change) >= abs(ai.claude_change)
                else ai.claude_change
            ),
            codex_change=ai.codex_change,
            claude_change=ai.claude_change,
            codex_tps=ai.codex_tps,
            claude_tps=ai.claude_tps,
            open_apps=system.open_apps,
            git_changes=changed_files,
            active_app=app,
            event_serial=self._event_serial,
            event_kind=event_kind,
            event_strength=event_strength,
            meeting_active=meeting_active,
            source=f"auto:{app};git={changed_files};events={calendar.events_next_four_hours}",
        ).clamped()

    def _event(
        self,
        app: str,
        previous_app: str,
        changed_files: int,
        ai: AiTelemetry,
        system: SystemTelemetry,
        now: float,
    ) -> tuple[str, float]:
        event_kind = "none"
        event_strength = 0.0
        if self._sampled_once and app != previous_app:
            event_kind, event_strength = "app_switch", 0.52
        if self._last_git_changes is not None and changed_files != self._last_git_changes:
            delta = abs(changed_files - self._last_git_changes)
            event_kind, event_strength = "git_change", min(0.45 + delta * 0.08, 0.85)
        if system.cpu_load > 0.68 and self._last_cpu_load <= 0.68:
            event_kind, event_strength = "cpu_surge", 0.65
        if self._sampled_once and ai.movement >= 0.22 and now - self._last_ai_event_at >= 6.0:
            if abs(ai.codex_change) >= abs(ai.claude_change):
                source = "codex"
                change = ai.codex_change
            else:
                source = "claude"
                change = ai.claude_change
            direction = "burst" if change > 0 else "cooldown"
            event_kind = f"{source}_{direction}"
            event_strength = min(0.55 + ai.movement * 0.40, 0.95)
            self._last_ai_event_at = now

        if event_kind != "none":
            self._event_serial += 1
        self._last_git_changes = changed_files
        self._last_cpu_load = system.cpu_load
        self._sampled_once = True
        return event_kind, event_strength

    def _frontmost_application(self) -> str:
        script = (
            'ObjC.import("AppKit"); '
            "$.NSWorkspace.sharedWorkspace.frontmostApplication.localizedName.js"
        )
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            name = result.stdout.strip()
            return name or self.last_app
        except (OSError, subprocess.TimeoutExpired):
            return self.last_app

    def _changed_files(self) -> int:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode != 0:
                return 0
            return sum(1 for line in result.stdout.splitlines() if line.strip())
        except (OSError, subprocess.TimeoutExpired):
            return 0

    def _calendar_signal(self) -> CalendarSignal:
        # This AppleScript only returns timing aggregates. It never requests an
        # event's title, notes, location, calendar name, or attendees.
        script = r"""
set nowDate to current date
set horizonDate to nowDate + (4 * hours)
set eventCount to 0
set activeCount to 0
set soonestSeconds to 999999
tell application "Calendar"
    repeat with cal in calendars
        try
            set candidates to (every event of cal whose start date is less than horizonDate and end date is greater than nowDate)
            repeat with ev in candidates
                set isAllDay to false
                try
                    set isAllDay to allday event of ev
                end try
                if isAllDay is false then
                    set eventCount to eventCount + 1
                    set startsIn to (start date of ev) - nowDate
                    if startsIn >= 0 and startsIn < soonestSeconds then set soonestSeconds to startsIn
                    if (start date of ev) <= nowDate and (end date of ev) > nowDate then set activeCount to activeCount + 1
                end if
            end repeat
        end try
    end repeat
end tell
return (eventCount as text) & "|" & (soonestSeconds as text) & "|" & (activeCount as text)
"""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if result.returncode != 0:
                self.last_calendar_error = result.stderr.strip() or "Calendar access failed"
                return CalendarSignal()
            count, seconds, active = (part.strip() for part in result.stdout.strip().split("|"))
            seconds_value = float(seconds)
            minutes = None if seconds_value >= 999999 else seconds_value / 60.0
            self.last_calendar_error = None
            return CalendarSignal(int(count), minutes, int(active))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            self.last_calendar_error = str(error)
            return CalendarSignal()


class ContextSampler:
    """Refresh context away from the timing-sensitive music scheduler."""

    def __init__(self, collector: ContextCollector, interval: float = 3.0) -> None:
        self.collector = collector
        self.interval = max(0.5, interval)
        self._latest = DayState(source="collector:starting")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="dayphony-context",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def latest(self) -> DayState:
        with self._lock:
            return self._latest

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sampled = self.collector.sample()
            except Exception:
                # A collector is advisory: unexpected platform/API changes must
                # never take down the soundtrack or its real-time clock.
                sampled = None
            if sampled is not None:
                with self._lock:
                    self._latest = sampled
            self._stop.wait(self.interval)


def with_live_context(base: DayState, live: DayState) -> DayState:
    """Keep a manual musical scene while retaining live environmental signals."""

    return replace(
        base,
        cpu_load=live.cpu_load,
        memory_load=live.memory_load,
        app_switch_rate=live.app_switch_rate,
        ai_activity=live.ai_activity,
        ai_change=live.ai_change,
        codex_change=live.codex_change,
        claude_change=live.claude_change,
        codex_tps=live.codex_tps,
        claude_tps=live.claude_tps,
        open_apps=live.open_apps,
        git_changes=live.git_changes,
        active_app=live.active_app,
        event_serial=live.event_serial,
        event_kind=live.event_kind,
        event_strength=live.event_strength,
        meeting_active=live.meeting_active,
        source=f"{base.source}+live",
    )


def with_energy(state: DayState, energy: float) -> DayState:
    return replace(state, energy=_clamp(energy), source="manual:energy")


def _mix(current: float, target: float, amount: float) -> float:
    return current + (target - current) * amount


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _signed_clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))
