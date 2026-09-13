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
    "sonic-pi-blade",
    "sonic-pi-chiplead",
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
            "-u",
            str(self.port),
            "-B",
            "127.0.0.1",
            "-i",
            "0",
            "-o",
            "2",
            "--app-name",
            "Dayphony",
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
            raise AudioEngineError(
                self._failure_message("Audio server did not answer OSC /status")
            )

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
    phrase_number: int = -1
    progression_index: int = 0
    motif_index: int = 0
    last_event_serial: int = 0
    event_kind: str = "none"
    event_steps: int = 0
    section_variant: int = 0
    pending_harmonic_change: bool = False

    PROGRESSIONS = (
        (
            (
                (50, 53, 57, 60, 64),
                (46, 50, 53, 57, 60),
                (41, 45, 48, 52, 55),
                (48, 50, 55, 60, 64),
            ),
            (38, 34, 41, 36),
        ),
        (
            (
                (50, 53, 57, 60, 64),
                (48, 52, 55, 57, 62),
                (46, 50, 53, 57, 62),
                (45, 50, 52, 55, 58),
            ),
            (38, 36, 34, 33),
        ),
        (
            (
                (43, 46, 50, 53, 57),
                (46, 50, 53, 57, 60),
                (50, 53, 57, 60, 64),
                (45, 49, 52, 55, 58),
            ),
            (43, 34, 38, 33),
        ),
        (
            (
                (50, 53, 57, 60, 65),
                (41, 45, 48, 53, 57),
                (43, 46, 50, 53, 57),
                (48, 52, 55, 57, 62),
            ),
            (38, 41, 43, 36),
        ),
    )

    MOTIFS = (
        (62, 65, 69, 72, 69, 65, 64, 60),
        (69, 72, 74, 77, 76, 72, 69, 65),
        (65, 69, 67, 64, 62, 60, 64, 69),
        (74, 72, 69, 65, 67, 69, 64, 62),
        (62, 64, 65, 69, 72, 69, 67, 64),
    )

    BASS_PATTERNS = (
        (0, 4),
        (0, 3, 4, 6),
        (0, 2, 4, 5, 7),
        (0, 2, 3, 4, 6, 7),
    )

    MELODY_RHYTHMS = (
        (0, 4),
        (0, 3, 6),
        (0, 2, 4, 7),
        (1, 4, 6),
    )

    AI_PATTERNS = (
        (0, 4),
        (0, 3, 5),
        (0, 2, 4, 6),
        (1, 2, 4, 5, 7),
    )

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
        phrase = bar // 8
        phrase_bar = bar % 8
        energy = state.energy
        urgency = state.urgency
        focus = state.focus
        developed = min(1.0, bar / max(1, intro_bars))

        if phrase != self.phrase_number:
            self.phrase_number = phrase
            signature = _stable_number(state.active_app)
            activity = int(state.cpu_load * 7 + state.ai_activity * 11 + state.open_apps / 4)
            self.progression_index = (phrase + signature + activity) % len(self.PROGRESSIONS)
            self.motif_index = (phrase * 2 + signature + state.git_changes) % len(self.MOTIFS)

        if state.event_serial != self.last_event_serial:
            self.last_event_serial = state.event_serial
            self.event_kind = state.event_kind
            if self.event_kind.startswith(("codex_", "claude_")) or self.event_kind == "ai_burst":
                self.event_steps = max(16, round(12 + state.event_strength * 8))
                self.section_variant = state.event_serial % 4
                self.pending_harmonic_change = True
            else:
                self.event_steps = max(2, round(2 + state.event_strength * 5))

        harmony_changed = False
        if beat8 == 0 and self.pending_harmonic_change:
            source_offset = 1 if self.event_kind.startswith("codex_") else 2
            self.progression_index = (
                self.progression_index + source_offset + self.section_variant
            ) % len(self.PROGRESSIONS)
            self.motif_index = (self.motif_index + source_offset + self.section_variant) % len(
                self.MOTIFS
            )
            self.pending_harmonic_change = False
            harmony_changed = True

        progression, roots = self.PROGRESSIONS[self.progression_index]
        chord_index = (bar // 2) % len(progression)
        chord = progression[chord_index]
        root = roots[chord_index]
        clarity = 1.0 - state.memory_load * 0.42
        context_motion = min(1.0, state.cpu_load * 0.65 + state.ai_activity * 0.75)
        variation = self.section_variant
        burst_active = self.event_steps > 0 and self.event_kind.endswith("burst")
        cooldown_active = self.event_steps > 0 and self.event_kind.endswith("cooldown")

        # Long overlapping chords keep the bed continuous. A new chord arrives
        # every two bars. Voicing, width and brightness evolve by phrase.
        if beat8 == 0 and (bar % 2 == 0 or harmony_changed):
            chord_seconds = self.seconds_for_beats(7.5, state)
            section_gain = 0.68 if cooldown_active else (1.15 if burst_active else 1.0)
            pad_amp = (0.016 + 0.013 * energy) * (0.88 + clarity * 0.12) * section_gain
            section_cutoff = -13 if cooldown_active else (10 if burst_active else 0)
            cutoff = ((69 + focus * 19 + energy * 9) * clarity) + section_cutoff
            width = 0.16 + min(state.open_apps, 18) / 18.0 * 0.12
            rotation = phrase % len(chord)
            voicing = chord[rotation:] + chord[:rotation]
            for voice, note in enumerate(voicing):
                if voice == 0 and rotation > 1:
                    note -= 12
                pan = (voice - 2) * width
                self._synth(
                    "sonic-pi-prophet",
                    note=float(note),
                    amp=pad_amp,
                    pan=pan,
                    attack=0.9 + focus * 0.8,
                    sustain=max(1.0, chord_seconds - 2.8),
                    release=2.1,
                    cutoff=cutoff,
                    res=0.78,
                )

        # Bass pattern banks preserve the downbeat while adding bar-scale turns.
        density = min(3, int(energy * 2.7 + urgency * 1.4 + state.cpu_load))
        bass_pattern = self.BASS_PATTERNS[
            (density + phrase_bar // 2 + variation) % len(self.BASS_PATTERNS)
        ]
        if burst_active:
            bass_pattern = self.BASS_PATTERNS[3]
        elif cooldown_active:
            bass_pattern = self.BASS_PATTERNS[0]
        bass_allowed = developed > 0.25 and beat8 in bass_pattern
        if bass_allowed:
            turn = beat8 in (5, 7) and phrase_bar in (3, 7)
            bass_note = root + (12 if turn or (beat8 == 6 and state.scene == "flow") else 0)
            bass_gain = 1.18 if burst_active else (0.68 if cooldown_active else 1.0)
            self._synth(
                "sonic-pi-subpulse",
                note=float(bass_note),
                amp=(0.040 + energy * 0.047) * developed * bass_gain,
                pan=0.0,
                attack=0.01,
                sustain=0.10,
                release=0.22 + focus * 0.18,
                cutoff=62 + energy * 15 + context_motion * 5,
                pulse_width=0.38 + (phrase % 3) * 0.04,
                sub_amp=0.9,
            )

        # The drum layers are density controls over a continuous grid. They do
        # not own the clock, so changing context cannot restart the song.
        if developed >= 0.72:
            syncopated_kick = (phrase + phrase_bar + variation) % 3 == 0 and beat8 == 3
            if burst_active:
                syncopated_kick = beat8 in (3, 6)
            if (
                beat8 == 0
                or (beat8 == 4 and energy > 0.38)
                or (
                    (urgency > 0.72 or state.cpu_load > 0.62 or burst_active)
                    and (beat8 in (6,) or syncopated_kick)
                )
            ):
                self._synth(
                    "sonic-pi-sc808_bassdrum",
                    note=34.0 + urgency * 2.0,
                    amp=(0.090 + energy * 0.075) * developed * (1.18 if burst_active else 1.0),
                    decay=0.55 + energy * 0.20,
                )
            snare_slots = (2,) if cooldown_active else (2, 6)
            if beat8 in snare_slots and energy > 0.44:
                self._synth(
                    "sonic-pi-sc808_snare",
                    amp=(0.025 + energy * 0.025) * (1.20 if burst_active else 1.0),
                    decay=0.22,
                    mix=0.78,
                    pan=(-0.08 if beat8 == 2 else 0.08),
                )
            hat_threshold = 0.26 if beat8 % 2 else 0.62
            hat_allowed = not cooldown_active or beat8 in (3, 7)
            if energy > hat_threshold and beat8 != 0 and hat_allowed:
                accent = 1.25 if beat8 in (3, 7) else 1.0
                self._synth(
                    "sonic-pi-sc808_closed_hihat",
                    amp=(0.010 + energy * 0.013) * accent * (1.35 if burst_active else 1.0),
                    decay=0.09 + urgency * 0.05,
                    pan=-0.22 if beat8 % 4 == 1 else 0.22,
                )
            if (
                (energy > 0.70 or state.app_switch_rate > 0.45 or burst_active)
                and beat8 == 7
                and phrase_bar in (3, 7)
            ):
                self._synth("sonic-pi-sc808_open_hihat", amp=0.018, decay=0.22, pan=0.28)

        # A family of related hooks rotates only at phrase boundaries. Rhythmic
        # masks vary by bar, yielding repetition with recognizable development.
        motif = self.MOTIFS[self.motif_index]
        note_index = (phrase_bar * 2 + beat8 // 2) % len(motif)
        rhythm = self.MELODY_RHYTHMS[
            (phrase + phrase_bar // 2 + variation) % len(self.MELODY_RHYTHMS)
        ]
        melody_slot = beat8 in rhythm and (beat8 in (0, 4) or energy > 0.56)
        if developed >= 0.98 and melody_slot and state.scene != "recovery" and not cooldown_active:
            note = motif[note_index]
            if burst_active or (state.scene == "pressure" and phrase_bar >= 4):
                note += 12
            self._synth(
                "sonic-pi-rhodey",
                note=float(note - 12),  # this SynthDef sounds one octave above its note input
                amp=(0.027 + energy * 0.025) * clarity * (1.20 if burst_active else 1.0),
                pan=-0.30 if note_index % 2 == 0 else 0.30,
                attack=0.01,
                decay=0.42,
                sustain=0.06,
                release=0.65 + focus * 0.30,
                vel=0.55 + energy * 0.25,
                mod_index=0.16 + urgency * 0.13,
                mix=0.24,
            )

        # Codex and Claude have distinct voices. A meaningful rate change
        # advances `variation`, replacing the rhythm instead of slightly
        # changing an already-saturated amplitude.
        codex_pattern = self.AI_PATTERNS[(variation + phrase_bar // 2) % len(self.AI_PATTERNS)]
        claude_pattern = self.AI_PATTERNS[
            (variation + phrase_bar // 2 + 2) % len(self.AI_PATTERNS)
        ]
        if burst_active:
            codex_pattern = self.AI_PATTERNS[3 if self.event_kind.startswith("codex_") else 0]
            claude_pattern = self.AI_PATTERNS[3 if self.event_kind.startswith("claude_") else 0]
        elif cooldown_active:
            codex_pattern = self.AI_PATTERNS[0] if self.event_kind.startswith("codex_") else ()
            claude_pattern = self.AI_PATTERNS[0] if self.event_kind.startswith("claude_") else ()
        if developed >= 0.98 and clarity > 0.58 and state.codex_tps > 0 and beat8 in codex_pattern:
            codex_index = (beat8 + phrase_bar * 2 + variation) % len(chord)
            codex_note = chord[codex_index] + 12
            self._synth(
                "sonic-pi-chiplead",
                note=float(codex_note),
                amp=(0.026 + state.ai_activity * 0.018)
                * clarity
                * (1.45 if burst_active else 1.0),
                attack=0.01,
                sustain=0.02,
                release=0.16 + focus * 0.18,
                pan=-0.52 + codex_index * 0.08,
            )
        if (
            developed >= 0.98
            and clarity > 0.58
            and state.claude_tps > 0
            and beat8 in claude_pattern
        ):
            claude_index = (7 - beat8 + phrase_bar + variation) % len(chord)
            claude_note = chord[claude_index] + 12
            self._synth(
                "sonic-pi-blade",
                note=float(claude_note),
                amp=(0.023 + state.ai_activity * 0.017)
                * clarity
                * (1.45 if burst_active else 1.0),
                pan=0.50 - claude_index * 0.06,
                attack=0.01,
                sustain=0.04,
                release=0.36,
                cutoff=86 + state.ai_activity * 18,
            )

        # Phrase turnarounds make section boundaries legible without stopping
        # the clock or resorting to abrupt scene-change stingers.
        if developed >= 0.98 and phrase_bar == 7 and beat8 in (5, 6, 7):
            fill_note = chord[(beat8 - 5 + phrase) % len(chord)] + 12
            self._synth(
                "sonic-pi-rhodey",
                note=float(fill_note - 12),
                amp=(0.013 + energy * 0.010) * clarity,
                pan=(beat8 - 6) * 0.26,
                attack=0.01,
                decay=0.18,
                sustain=0.02,
                release=0.30,
                vel=0.48,
                mod_index=0.18,
                mix=0.18,
            )

        if self.event_steps > 0:
            self._play_event_accent(beat8, chord, root, state, clarity)
            self.event_steps -= 1

        # Recovery retains a quiet pulse rather than dropping into dead air.
        if state.scene == "recovery" and beat8 in (0, 4):
            self._synth(
                "sonic-pi-beep",
                note=float(roots[chord_index] + 12),
                amp=0.020,
                attack=0.06,
                sustain=0.05,
                release=0.65,
                pan=0.0,
            )

    def _play_event_accent(
        self,
        beat8: int,
        chord: tuple[int, ...],
        root: int,
        state: DayState,
        clarity: float,
    ) -> None:
        position = max(0, self.event_steps - 1)
        if self.event_kind == "cpu_surge" and position % 2 == 0:
            self._synth(
                "sonic-pi-subpulse",
                note=float(root),
                amp=0.055,
                attack=0.01,
                sustain=0.08,
                release=0.28,
                cutoff=70 + state.cpu_load * 12,
                pulse_width=0.36,
                sub_amp=0.95,
            )
        elif self.event_kind == "git_change" and position in (1, 3, 5):
            note = chord[(beat8 + position) % len(chord)] + 12
            self._synth(
                "sonic-pi-rhodey",
                note=float(note - 12),
                amp=0.030 * clarity,
                pan=-0.28 + position * 0.10,
                attack=0.01,
                decay=0.22,
                sustain=0.03,
                release=0.50,
                vel=0.58,
                mod_index=0.20,
                mix=0.20,
            )
        elif self.event_kind in ("ai_burst", "codex_burst", "codex_cooldown"):
            cooling = self.event_kind.endswith("cooldown")
            if cooling and position % 2:
                return
            direction = -1 if cooling else 1
            note = chord[(beat8 + direction * position) % len(chord)] + (12 if cooling else 24)
            self._synth(
                "sonic-pi-chiplead",
                note=float(note),
                amp=(0.045 if cooling else 0.075) * clarity,
                pan=-0.58 + (position % 4) * 0.22,
                attack=0.01,
                sustain=0.02,
                release=0.24,
            )
        elif self.event_kind in ("claude_burst", "claude_cooldown"):
            cooling = self.event_kind.endswith("cooldown")
            if cooling and position % 2:
                return
            direction = -1 if cooling else 1
            note = chord[(beat8 + direction * position + 2) % len(chord)] + 12
            self._synth(
                "sonic-pi-blade",
                note=float(note),
                amp=(0.042 if cooling else 0.068) * clarity,
                pan=0.58 - (position % 4) * 0.20,
                attack=0.01,
                sustain=0.04,
                release=0.42,
                cutoff=94 if cooling else 110,
            )
        elif self.event_kind == "app_switch" and position in (0, 2):
            note = chord[(self.phrase_number + position) % len(chord)] + 12
            self._synth(
                "sonic-pi-rhodey",
                note=float(note - 12),
                amp=0.020 * clarity,
                pan=-0.20 if position else 0.20,
                attack=0.01,
                decay=0.20,
                sustain=0.03,
                release=0.44,
                vel=0.50,
                mod_index=0.16,
                mix=0.18,
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


def _stable_number(value: str) -> int:
    return sum((index + 1) * ord(character) for index, character in enumerate(value))
