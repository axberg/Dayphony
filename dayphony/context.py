from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path


SCENES = ("focus", "flow", "pressure", "recovery")


@dataclass(frozen=True)
class DayState:
    scene: str = "focus"
    focus: float = 0.8
    energy: float = 0.4
    urgency: float = 0.2
    social_load: float = 0.1
    meeting_active: bool = False
    source: str = "default"

    def clamped(self) -> "DayState":
        return replace(
            self,
            focus=_clamp(self.focus),
            energy=_clamp(self.energy),
            urgency=_clamp(self.urgency),
            social_load=_clamp(self.social_load),
        )

    def smooth_towards(self, target: "DayState", dt: float, tau: float) -> "DayState":
        amount = 1.0 if tau <= 0 else 1.0 - math.exp(-max(0.0, dt) / tau)
        return DayState(
            scene=target.scene,
            focus=_mix(self.focus, target.focus, amount),
            energy=_mix(self.energy, target.energy, amount),
            urgency=_mix(self.urgency, target.urgency, amount),
            social_load=_mix(self.social_load, target.social_load, amount),
            meeting_active=target.meeting_active,
            source=target.source,
        ).clamped()


PRESETS: dict[str, DayState] = {
    "focus": DayState("focus", focus=0.92, energy=0.38, urgency=0.12, social_load=0.05, source="preset"),
    "flow": DayState("flow", focus=0.86, energy=0.68, urgency=0.26, social_load=0.08, source="preset"),
    "pressure": DayState("pressure", focus=0.58, energy=0.92, urgency=0.88, social_load=0.35, source="preset"),
    "recovery": DayState("recovery", focus=0.45, energy=0.22, urgency=0.04, social_load=0.04, source="preset"),
}


@dataclass(frozen=True)
class CalendarSignal:
    events_next_four_hours: int = 0
    minutes_to_next: float | None = None
    active_events: int = 0


class ContextCollector:
    def __init__(self, workspace: Path, include_calendar: bool = False) -> None:
        self.workspace = workspace.resolve()
        self.include_calendar = include_calendar
        self.last_app = "Unknown"
        self.last_calendar_error: str | None = None

    def sample(self) -> DayState:
        app = self._frontmost_application()
        self.last_app = app
        app_key = app.lower()

        if any(name in app_key for name in ("code", "cursor", "xcode", "terminal", "iterm", "warp")):
            focus, energy, urgency, social = 0.88, 0.56, 0.22, 0.05
        elif any(name in app_key for name in ("teams", "slack", "mail", "outlook", "zoom")):
            focus, energy, urgency, social = 0.36, 0.58, 0.52, 0.86
        elif any(name in app_key for name in ("keynote", "powerpoint", "meet")):
            focus, energy, urgency, social = 0.52, 0.62, 0.48, 0.66
        elif any(name in app_key for name in ("safari", "chrome", "firefox", "edge")):
            focus, energy, urgency, social = 0.62, 0.48, 0.30, 0.15
        else:
            focus, energy, urgency, social = 0.68, 0.45, 0.22, 0.10

        changed_files = self._changed_files()
        workload = min(changed_files / 12.0, 1.0)
        energy += workload * 0.12
        urgency += workload * 0.16

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
            meeting_active=meeting_active,
            source=f"auto:{app};git={changed_files};events={calendar.events_next_four_hours}",
        ).clamped()

    def _frontmost_application(self) -> str:
        script = (
            'ObjC.import("AppKit"); '
            '$.NSWorkspace.sharedWorkspace.frontmostApplication.localizedName.js'
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
        script = r'''
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
'''
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


def with_energy(state: DayState, energy: float) -> DayState:
    return replace(state, energy=_clamp(energy), source="manual:energy")


def _mix(current: float, target: float, amount: float) -> float:
    return current + (target - current) * amount


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
