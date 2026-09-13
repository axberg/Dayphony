# Architecture

Dayphony separates personal context, musical decisions, and real-time audio so each
layer can evolve without giving the audio server access to private data.

```text
Context adapters                 Control plane                 Audio plane
─────────────────               ─────────────────             ───────────────
frontmost app ─┐
system load ───┤
open apps ─────┼─> ContextCollector ─> DayState ─> director ─OSC─> SuperSonic
Git activity ──┤         │              aggregates      │          │
calendar time ─┤         └─ raw data ends here          └─ clock   └─ speakers
AI counters ───┤
manual input ──┘
```

## Context layer

`ContextCollector` owns platform-specific access. It emits a `DayState` containing
only normalized musical controls, aggregate counters, event signals, and scene
fields. Raw window, repository, calendar, or session-log records do not cross this
boundary.

The current macOS adapters use `NSWorkspace` for the frontmost application, Git's
porcelain status for a changed-file count, `ps` and `memory_pressure` for system
load, and an opt-in AppleScript query for calendar timing aggregates. An optional
local token adapter reduces recent Codex and Claude session records to rolling token
rates without retaining message content.

## Director

The command-line process owns one monotonic transport. A background worker samples
context every three seconds, while the timing thread only reads immutable snapshots.
Numeric changes are smoothed and scene changes wait for an eight-bar phrase
boundary. Manual scenes override inferred musical state while live environment
signals remain active.

The director, not the audio engine, is the intended MCP boundary. This keeps agent
latency and failures outside the real-time loop.

## Audio layer

Dayphony starts a private SuperSonic process bound to loopback on an ephemeral UDP
port. Audio input is disabled. It loads selected SynthDefs from the user's Sonic Pi
installation and creates all voices in a dedicated node group so pause and shutdown
can free them safely.

The scheduler uses an eighth-note grid with one continuous clock. Long pad releases
overlap chord changes; bass preserves the pulse; several progression, motif, rhythm,
and arpeggio banks rotate deterministically at musical boundaries. Meaningful AI
rate changes immediately select a different variation lane: Codex drives a
left-panned chiplead and Claude a right-panned blade counterline. Bursts and
cooldowns create contrasting dense/bright and sparse/dark two-bar mini-sections,
including a bar-aligned harmonic and motif change. Context events do not restart
the arrangement.

## Failure boundaries

- Missing Calendar permission degrades to calendar-free operation.
- Missing Git metadata produces a zero workload signal.
- Missing or changed Codex/Claude local logs produce zero token activity.
- Slow context adapters cannot block the music scheduler.
- Missing audio assets stops startup with an actionable error.
- `pause`, `quit`, signals, and normal shutdown clear the dedicated synth group.
- The OSC command port binds to `127.0.0.1`, not the network.
