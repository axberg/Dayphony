# Architecture

Dayphony separates personal context, musical decisions, and real-time audio so each
layer can evolve without giving the audio server access to private data.

```text
Context adapters                 Control plane                 Audio plane
─────────────────               ─────────────────             ───────────────
frontmost app ─┐
Git activity ──┼─> ContextCollector ─> DayState ─> director ─OSC─> SuperSonic
calendar time ─┤         │              0..1 values     │          │
manual input ──┘         └─ raw data ends here          └─ clock   └─ speakers
```

## Context layer

`ContextCollector` owns platform-specific access. It emits a `DayState` containing
only normalized focus, energy, urgency, social-load, meeting, and scene fields. Raw
window, repository, or calendar values should not cross this boundary.

The current macOS adapters use `NSWorkspace` for the frontmost application, Git's
porcelain status for a changed-file count, and an opt-in AppleScript query for
calendar timing aggregates.

## Director

The command-line process owns one monotonic transport. It samples context every 15
seconds, smooths numeric changes, and holds scene changes until an eight-bar phrase
boundary. User commands override inferred state.

The director, not the audio engine, is the intended MCP boundary. This keeps agent
latency and failures outside the real-time loop.

## Audio layer

Dayphony starts a private SuperSonic process bound to loopback on an ephemeral UDP
port. Audio input is disabled. It loads selected SynthDefs from the user's Sonic Pi
installation and creates all voices in a dedicated node group so pause and shutdown
can free them safely.

The scheduler uses an eighth-note grid with one continuous clock. Long pad releases
overlap chord changes; bass preserves the pulse; drums and melody change density in
response to the smoothed state.

## Failure boundaries

- Missing Calendar permission degrades to calendar-free operation.
- Missing Git metadata produces a zero workload signal.
- Missing audio assets stops startup with an actionable error.
- `pause`, `quit`, signals, and normal shutdown clear the dedicated synth group.
- The OSC command port binds to `127.0.0.1`, not the network.
