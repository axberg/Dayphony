# Dayphony

**A private, adaptive soundtrack for your working day.**

Dayphony turns lightweight signals—your frontmost app, open applications, CPU and
memory pressure, local Git activity, calendar timing, optional local AI token rates,
and manual input—into a continuously evolving procedural soundtrack. The music
keeps one clock running and changes gradually, so context switches do not restart
the song or produce notification-like bursts.

> [!IMPORTANT]
> Dayphony is an early macOS prototype. Its audio engine currently uses the
> SuperSonic server and SynthDefs bundled with Sonic Pi 4. The project does not
> bundle or redistribute Sonic Pi assets.

## Why Dayphony?

Most generated music tools create tracks. Dayphony creates a **musical state** that
lives alongside your day:

- focus makes the arrangement calmer and more predictable;
- flow adds motion without breaking concentration;
- pressure increases rhythmic density and brightness;
- recovery opens space while preserving a quiet pulse;
- CPU and app switching reshape rhythmic motion;
- meaningful token-rate changes switch patterns and trigger source-specific replies:
  Codex is a left-side digital pulse, while Claude is a right-side electric-key line;
- AI bursts open a bright, dense two-bar mini-section; cooldowns answer with a
  darker, deliberately sparse two-bar section;
- Git changes and context events become short, harmonically related accents;
- changes are smoothed and structural transitions wait for phrase boundaries.

Raw context stays on your computer. The music engine receives only normalized
values such as `focus`, `energy`, and `urgency`.

## Requirements

- macOS
- Python 3.10 or newer
- [Sonic Pi](https://sonic-pi.net/) installed in `/Applications`

Dayphony has no Python runtime dependencies. Close the Sonic Pi application before
starting Dayphony so the two audio servers do not compete for the output device.

## Quick start

Clone the repository and run the adaptive demo:

```bash
git clone https://github.com/axberg/Dayphony.git
cd Dayphony
./run --mode demo
```

The first four bars form a short introduction. The beat then remains continuous
while the demo moves through focus, flow, pressure, and recovery.

Use local context from the frontmost application and a Git workspace:

```bash
./run --mode auto --workspace /path/to/your/project
```

Calendar timing is explicitly opt-in because macOS will request Calendar automation
permission:

```bash
./run --mode auto --workspace /path/to/your/project --calendar
```

The Calendar adapter collects only whether a meeting is active, the number of timed
events in the next four hours, and minutes until the next event. It never requests
event titles, notes, locations, calendar names, or attendees. If access is denied,
Dayphony continues without calendar input.

To also estimate recent Codex and Claude token throughput from their local session
records, opt in explicitly:

```bash
./run --mode auto --workspace /path/to/your/project --ai-telemetry
```

This adapter reads only timestamps and numeric token counters; it does not retain
prompts, responses, tool calls, or file content. Codex and Claude do not currently
offer Dayphony a stable live local TPS interface, so this best-effort adapter may
need updating when either client changes its private log format. Missing or
unrecognized logs simply produce `0.0t/s`. The `ai-change` value in status output
shows the normalized rate movement currently driving musical changes.

## Live controls

Type a command and press Return while Dayphony is running:

```text
auto
focus
flow
pressure
recovery
energy 0.8
intensity 0.6
status
help
pause
resume
quit
```

`energy` and `intensity` are aliases. A manual value remains active until `auto` or
another scene is selected.

Useful flags:

```text
--duration SECONDS    stop automatically (useful for testing)
--demo-bars BARS      bars per scene in demo mode (default: 8)
--calendar            opt in to aggregate Calendar timing
--ai-telemetry        opt in to local Codex/Claude token-rate estimates
--quiet               print only errors
--dry-run             exercise context and arrangement without starting audio
```

Run `./run --help` for the complete command-line reference.

## Install as a CLI

An editable install exposes the `dayphony` command:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
dayphony --mode demo
```

For nonstandard Sonic Pi layouts, configure both paths:

```bash
export DAYPHONY_SUPERSONIC=/path/to/supersonic
export DAYPHONY_SYNTHDEFS=/path/to/compiled/synthdefs
```

## Architecture

```text
macOS / Git / Calendar / optional AI counters
                    │
                    ▼ background sampling
             ContextCollector ──> DayState ──> MusicDirector ──OSC──> SuperSonic
             (raw data local)      aggregates   musical timing       continuous audio
```

The OSC layer and musical scheduler are separate. A future MCP server can expose
supervisory tools such as `set_mode`, `set_intensity`, `pause`, and `status` without
putting an LLM or network request in the timing-sensitive audio loop.

See [Architecture](docs/architecture.md), [Privacy](docs/privacy.md), and the
[roadmap](ROADMAP.md) for more detail.

## Development

```bash
python3 -m unittest discover -s tests -v
./run --mode auto --dry-run --duration 2
```

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request. Security and
privacy reports should follow [SECURITY.md](SECURITY.md).

## License and acknowledgements

Dayphony's original source code is available under the [MIT License](LICENSE).

Dayphony depends on a separately installed copy of Sonic Pi and refers to compatible
SynthDefs by name. Sonic Pi and its bundled components remain under their respective
licenses and copyrights. See the [Sonic Pi repository](https://github.com/sonic-pi-net/sonic-pi)
for its license and source.
