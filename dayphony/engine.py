from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .context import DayState
from .osc import OscClient, OscError


DEFAULT_SONIC_PI_ROOT = Path("/Applications/Sonic Pi.app/Contents/Resources")

REQUIRED_SYNTHDEFS = (
    "sonic-pi-beep",
    "sonic-pi-rhodey",
    "sonic-pi-prophet",
    "sonic-pi-subpulse",
    "sonic-pi-sc808_bassdrum",
    "sonic-pi-sc808_snare",
    "sonic-pi-sc808_closed_hihat",
    "sonic-pi-sc808_open_hihat",
)


class AudioEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioPaths:
    supersonic: Path
    synthdefs: Path

    @classmethod
    def discover(cls) -> "AudioPaths":
        root = DEFAULT_SONIC_PI_ROOT
        return cls(
            supersonic=Path(
                os.environ.get(
                    "DAYPHONY_SUPERSONIC",
                    root / "app/server/native/Sonic Pi - SuperSonic",
                )
            ).expanduser(),
            synthdefs=Path(
                os.environ.get(
                    "DAYPHONY_SYNTHDEFS",
                    root / "etc/synthdefs/compiled",
                )
            ).expanduser(),
        )


class SuperSonicServer:
    def __init__(self, paths: AudioPaths | None = None) -> None:
        self.paths = paths or AudioPaths.discover()
        self.port = _available_udp_port()
        self.client = OscClient("127.0.0.1", self.port)
        self.process: subprocess.Popen[bytes] | None = None
        self.log = tempfile.TemporaryFile()

    def start(self) -> None:
        if not self.paths.supersonic.is_file():
            raise AudioEngineError(
                "SuperSonic was not found. Install Sonic Pi in /Applications or set "
                "DAYPHONY_SUPERSONIC and DAYPHONY_SYNTHDEFS."
            )
        missing = [
            name
            for name in REQUIRED_SYNTHDEFS
            if not (self.paths.synthdefs / f"{name}.scsyndef").is_file()
        ]
        if missing:
            raise AudioEngineError(f"Missing Sonic Pi SynthDefs: {', '.join(missing)}")

        command = [
            str(self.paths.supersonic),
            "-u", str(self.port),
            "-B", "127.0.0.1",
            "-i", "0",
            "-o", "2",
            "--app-name", "Dayphony",
        ]
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT)

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AudioEngineError(self._failure_message("Audio server exited during startup"))
            if self.client.server_is_ready():
                break
            time.sleep(0.10)
        else:
            raise AudioEngineError(self._failure_message("Audio server did not answer OSC /status"))

        for index, name in enumerate(REQUIRED_SYNTHDEFS, start=1):
            self.client.send("/d_load", str(self.paths.synthdefs / f"{name}.scsyndef"))
            self.client.sync(10_000 + index)

        self.client.send("/g_new", 1000, 0, 0)
        self.client.sync(20_000)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                self.client.send("/g_freeAll", 1000)
                self.client.send("/quit")
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self.client.close()
        self.log.close()

    def _failure_message(self, heading: str) -> str:
        self.log.seek(0)
        output = self.log.read().decode("utf-8", errors="replace")[-4000:]
        return f"{heading}.\n{output}" if output else heading


@dataclass
class MusicEngine:
    client: OscClient
    node_id: int = 2000
    muted: bool = False

    def panic(self) -> None:
        self.client.send("/g_freeAll", 1000)

    def set_muted(self, muted: bool) -> None:
        if muted and not self.muted:
            self.panic()
        self.muted = muted

    def play_step(self, step: int, state: DayState, intro_bars: int = 4) -> None:
        if self.muted:
            return

        beat8 = step % 8
        bar = step // 8
        phrase_bar = bar % 16
        energy = state.energy
        urgency = state.urgency
        focus = state.focus
        developed = min(1.0, bar / max(1, intro_bars))

        progression = (
            (50, 53, 57, 60, 64),  # Dm9
            (46, 50, 53, 57, 60),  # Bbmaj9
            (41, 45, 48, 52, 55),  # Fmaj9
            (48, 50, 55, 60, 64),  # Cadd9
        )
        roots = (38, 34, 41, 36)
        chord_index = (bar // 4) % len(progression)

        # Long overlapping chords keep the bed continuous. A new chord arrives
        # every four bars; releases overlap the next harmony.
        if beat8 == 0 and bar % 4 == 0:
            chord_seconds = self.seconds_for_beats(15.5, state)
            pad_amp = 0.018 + (0.014 * energy)
            cutoff = 72 + (focus * 18) + (energy * 8)
            for voice, note in enumerate(progression[chord_index]):
                pan = (voice - 2) * 0.24
                self._synth(
                    "sonic-pi-prophet",
                    note=float(note), amp=pad_amp, pan=pan,
                    attack=1.8, sustain=max(1.0, chord_seconds - 4.0), release=2.8,
                    cutoff=cutoff, res=0.78,
                )

        # A steady bass pulse fades in during the introduction and remains the
        # rhythmic anchor. Pressure adds eighth-note movement, never pauses.
        bass_allowed = developed > 0.25 and (beat8 % 2 == 0 or (urgency > 0.76 and beat8 in (3, 7)))
        if bass_allowed:
            root = roots[chord_index]
            bass_note = root + (12 if beat8 == 6 and state.scene == "flow" else 0)
            self._synth(
                "sonic-pi-subpulse",
                note=float(bass_note),
                amp=(0.045 + energy * 0.050) * developed,
                pan=0.0, attack=0.01, sustain=0.10,
                release=0.22 + focus * 0.18,
                cutoff=64 + energy * 14, pulse_width=0.42, sub_amp=0.9,
            )

        # The drum layers are density controls over a continuous grid. They do
        # not own the clock, so changing context cannot restart the song.
        if developed >= 0.72:
            if beat8 == 0 or (beat8 == 4 and energy > 0.42) or (urgency > 0.80 and beat8 in (2, 6)):
                self._synth(
                    "sonic-pi-sc808_bassdrum",
                    note=34.0 + urgency * 2.0,
                    amp=(0.10 + energy * 0.08) * developed,
                    decay=0.55 + energy * 0.20,
                )
            if beat8 in (2, 6) and energy > 0.50:
                self._synth(
                    "sonic-pi-sc808_snare",
                    amp=0.025 + energy * 0.025,
                    decay=0.22, mix=0.78, pan=(-0.08 if beat8 == 2 else 0.08),
                )
            hat_threshold = 0.30 if beat8 % 2 else 0.68
            if energy > hat_threshold and beat8 != 0:
                accent = 1.25 if beat8 in (3, 7) else 1.0
                self._synth(
                    "sonic-pi-sc808_closed_hihat",
                    amp=(0.010 + energy * 0.013) * accent,
                    decay=0.09 + urgency * 0.05,
                    pan=-0.22 if beat8 % 4 == 1 else 0.22,
                )
            if energy > 0.78 and beat8 == 7 and phrase_bar in (3, 7, 11, 15):
                self._synth("sonic-pi-sc808_open_hihat", amp=0.018, decay=0.22, pan=0.28)

        # A sparse, repeatable motif becomes more articulate with energy. It is
        # deterministic so the listener can learn it instead of hearing random
        # notification-like notes.
        motif = (62, 65, 69, 72, 69, 65, 64, 60)
        motif_index = (bar * 2 + beat8 // 4) % len(motif)
        melody_slot = beat8 in (0, 4) or (energy > 0.72 and beat8 in (2, 6))
        if developed >= 0.98 and melody_slot and state.scene != "recovery":
            note = motif[motif_index]
            if state.scene == "pressure" and phrase_bar >= 8:
                note += 12
            self._synth(
                "sonic-pi-rhodey",
                note=float(note - 12),  # this SynthDef sounds one octave above its note input
                amp=0.030 + energy * 0.027,
                pan=-0.30 if motif_index % 2 == 0 else 0.30,
                attack=0.01, decay=0.42, sustain=0.06,
                release=0.65 + focus * 0.30,
                vel=0.55 + energy * 0.25,
                mod_index=0.16 + urgency * 0.13,
                mix=0.24,
            )

        # Recovery retains a quiet pulse rather than dropping into dead air.
        if state.scene == "recovery" and beat8 in (0, 4):
            self._synth(
                "sonic-pi-beep",
                note=float(roots[chord_index] + 12), amp=0.020,
                attack=0.06, sustain=0.05, release=0.65, pan=0.0,
            )

    @staticmethod
    def bpm(state: DayState) -> float:
        return 78.0 + state.energy * 8.0 + state.urgency * 8.0

    def seconds_for_beats(self, beats: float, state: DayState) -> float:
        return beats * 60.0 / self.bpm(state)

    def _synth(self, name: str, **parameters: float) -> None:
        self.node_id += 1
        if self.node_id > 2_000_000_000:
            self.node_id = 2000
        arguments: list[object] = [name, self.node_id, 0, 1000]
        for key, value in parameters.items():
            arguments.extend((key, float(value)))
        self.client.send("/s_new", *arguments)


def _available_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
