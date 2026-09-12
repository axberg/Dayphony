from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from .context import ContextCollector, DayState, PRESETS, SCENES, with_energy
from .engine import AudioEngineError, MusicEngine, SuperSonicServer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dayphony",
        description="Dayphony: a continuously adapting soundtrack for your day",
    )
    parser.add_argument("--mode", choices=("auto", "demo", *SCENES), default="auto")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--calendar", action="store_true", help="opt in to aggregate Calendar timing")
    parser.add_argument("--duration", type=float, help="stop automatically after this many seconds")
    parser.add_argument("--demo-bars", type=int, default=8, help="bars per scene in demo mode")
    parser.add_argument("--dry-run", action="store_true", help="run without starting the audio server")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def input_worker(commands: queue.Queue[str]) -> None:
    while True:
        try:
            line = sys.stdin.readline()
        except (OSError, KeyboardInterrupt):
            return
        if not line:
            return
        commands.put(line.strip())


def describe(state: DayState, app: str, mode: str) -> str:
    return (
        f"mode={mode:<8} scene={state.scene:<8} app={app:<18} "
        f"focus={state.focus:.2f} energy={state.energy:.2f} "
        f"urgency={state.urgency:.2f} social={state.social_load:.2f}"
    )


def main() -> int:
    args = parse_args()
    collector = ContextCollector(args.workspace, include_calendar=args.calendar)
    commands: queue.Queue[str] = queue.Queue()
    threading.Thread(target=input_worker, args=(commands,), daemon=True).start()

    stop_requested = False

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    server: SuperSonicServer | None = None
    engine: MusicEngine | None = None
    try:
        if not args.dry_run:
            server = SuperSonicServer()
            server.start()
            engine = MusicEngine(server.client)

        mode = args.mode
        target = PRESETS["focus"] if mode == "demo" else (
            collector.sample() if mode == "auto" else PRESETS[mode]
        )
        current = replace(target, energy=0.12, urgency=0.05, source="startup")
        manual_energy: float | None = None
        muted = False
        step = 0
        started = time.monotonic()
        last_tick = started
        last_context_sample = 0.0
        next_tick = started
        last_reported_bar = -1
        demo_scene_index = 0

        if not args.quiet:
            print("Dayphony is running. Commands: auto, focus, flow, pressure, recovery,")
            print("energy <0..1>, pause, resume, status, quit")

        while not stop_requested:
            now = time.monotonic()
            if args.duration is not None and now - started >= args.duration:
                break

            while True:
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                if not command:
                    continue
                parts = command.lower().split()
                name = parts[0]
                if name in ("quit", "exit", "stop"):
                    stop_requested = True
                elif name == "pause":
                    muted = True
                    if engine:
                        engine.set_muted(True)
                    print("Soundtrack paused")
                elif name == "resume":
                    muted = False
                    if engine:
                        engine.set_muted(False)
                    print("Soundtrack resumed")
                elif name == "auto":
                    mode = "auto"
                    manual_energy = None
                    target = collector.sample()
                    print("Automatic context mode")
                elif name in SCENES:
                    mode = name
                    manual_energy = None
                    target = PRESETS[name]
                    print(f"Scene requested: {name} (musical change is smoothed)")
                elif name in ("energy", "intensity") and len(parts) == 2:
                    try:
                        manual_energy = max(0.0, min(1.0, float(parts[1])))
                        target = with_energy(target, manual_energy)
                        print(f"Energy requested: {manual_energy:.2f}")
                    except ValueError:
                        print("Energy must be a number from 0 to 1")
                elif name == "status":
                    print(describe(current, collector.last_app, mode))
                else:
                    print(f"Unknown command: {command}")

            if stop_requested:
                break

            bar = step // 8
            phrase_boundary = step % (8 * 8) == 0
            demo_boundary = step % (8 * max(1, args.demo_bars)) == 0

            if mode == "demo" and demo_boundary and bar > 0:
                demo_scene_index = (demo_scene_index + 1) % len(SCENES)
                target = PRESETS[SCENES[demo_scene_index]]
            elif mode == "auto" and now - last_context_sample >= 15.0:
                sampled = collector.sample()
                # Timbre/density can drift immediately. Scene changes are held
                # until a phrase boundary to preserve musical continuity.
                scene = sampled.scene if phrase_boundary else target.scene
                target = replace(sampled, scene=scene)
                last_context_sample = now

            if manual_energy is not None:
                target = with_energy(target, manual_energy)

            if now >= next_tick:
                dt = max(0.001, now - last_tick)
                tau = 7.0 if mode == "demo" else 35.0
                current = current.smooth_towards(target, dt, tau)

                meeting_mute = mode == "auto" and current.meeting_active
                if engine:
                    engine.set_muted(muted or meeting_mute)
                    engine.play_step(step, current)

                if not args.quiet and bar != last_reported_bar and bar % 4 == 0:
                    print(describe(current, collector.last_app, mode))
                    last_reported_bar = bar

                step += 1
                last_tick = now
                eighth_seconds = 30.0 / MusicEngine.bpm(current)
                next_tick += eighth_seconds
                if next_tick < now - 0.5:
                    next_tick = now + eighth_seconds

            time.sleep(max(0.005, min(0.03, next_tick - time.monotonic())))

        return 0
    except (AudioEngineError, OscError) as error:
        print(f"Audio startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        if engine:
            engine.panic()
        if server:
            server.stop()
        if not args.quiet:
            print("Dayphony stopped")


if __name__ == "__main__":
    raise SystemExit(main())
