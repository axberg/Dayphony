from __future__ import annotations

import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dayphony.context import ContextCollector, DayState, PRESETS, with_energy
from dayphony.engine import AudioPaths, MusicEngine
from dayphony.osc import decode_address, encode_message
from dayphony.telemetry import AiTelemetry, LocalTokenMonitor, SystemTelemetry


class FakeSystemMonitor:
    def __init__(self, value: SystemTelemetry | None = None) -> None:
        self.value = value or SystemTelemetry()

    def sample(self) -> SystemTelemetry:
        return self.value


class FakeTokenMonitor:
    def __init__(self, value: AiTelemetry) -> None:
        self.value = value

    def sample(self) -> AiTelemetry:
        return self.value


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def send(self, address: str, *arguments: object) -> None:
        self.messages.append((address, arguments))


class OscTests(unittest.TestCase):
    def test_encodes_scsynth_message(self) -> None:
        packet = encode_message("/s_new", "sonic-pi-beep", 2001, 0, 1000, "note", 60.0)
        self.assertEqual(decode_address(packet), "/s_new")
        self.assertIn(b",siiisf\0", packet)
        self.assertTrue(packet.endswith(struct.pack(">f", 60.0)))
        self.assertEqual(len(packet) % 4, 0)

    def test_rejects_invalid_address(self) -> None:
        with self.assertRaises(ValueError):
            encode_message("status")


class StateTests(unittest.TestCase):
    def test_smoothing_is_bounded_and_moves_toward_target(self) -> None:
        current = PRESETS["recovery"]
        target = PRESETS["pressure"]
        result = current.smooth_towards(target, dt=5.0, tau=10.0)
        self.assertGreater(result.energy, current.energy)
        self.assertLess(result.energy, target.energy)
        self.assertEqual(result.scene, "pressure")

    def test_energy_override_is_clamped(self) -> None:
        self.assertEqual(with_energy(PRESETS["focus"], 4.0).energy, 1.0)
        self.assertEqual(with_energy(PRESETS["focus"], -2.0).energy, 0.0)

    @patch.object(ContextCollector, "_frontmost_application", return_value="Visual Studio Code")
    @patch.object(ContextCollector, "_changed_files", return_value=3)
    def test_development_app_produces_focus_state(self, _git: object, _app: object) -> None:
        collector = ContextCollector(Path.cwd(), system_monitor=FakeSystemMonitor())
        state = collector.sample()
        self.assertIn(state.scene, ("focus", "flow"))
        self.assertGreater(state.focus, 0.8)
        self.assertFalse(state.meeting_active)

    def test_system_and_token_activity_reach_day_state(self) -> None:
        system = SystemTelemetry(cpu_load=0.72, memory_load=0.81, open_apps=14, coding_apps=3)
        tokens = AiTelemetry(
            codex_tps=18.0,
            claude_tps=4.0,
            codex_change=0.6,
            claude_change=-0.2,
        )
        collector = ContextCollector(
            Path.cwd(),
            system_monitor=FakeSystemMonitor(system),
            token_monitor=FakeTokenMonitor(tokens),
        )
        with (
            patch.object(collector, "_frontmost_application", return_value="Terminal"),
            patch.object(collector, "_changed_files", return_value=2),
        ):
            state = collector.sample()
        self.assertEqual(state.open_apps, 14)
        self.assertEqual(state.codex_tps, 18.0)
        self.assertEqual(state.claude_tps, 4.0)
        self.assertAlmostEqual(state.cpu_load, 0.72)
        self.assertAlmostEqual(state.ai_change, 0.6)
        self.assertGreater(state.ai_activity, 0.0)

    def test_git_change_becomes_a_discrete_event(self) -> None:
        collector = ContextCollector(Path.cwd(), system_monitor=FakeSystemMonitor())
        with (
            patch.object(collector, "_frontmost_application", return_value="Terminal"),
            patch.object(collector, "_changed_files", side_effect=(0, 2)),
        ):
            collector.sample()
            state = collector.sample()
        self.assertEqual(state.event_kind, "git_change")
        self.assertEqual(state.event_serial, 1)

    def test_ai_rate_change_becomes_a_source_specific_event(self) -> None:
        tokens = AiTelemetry(codex_tps=800.0, codex_change=0.75)
        collector = ContextCollector(
            Path.cwd(),
            system_monitor=FakeSystemMonitor(),
            token_monitor=FakeTokenMonitor(tokens),
        )
        with (
            patch.object(collector, "_frontmost_application", return_value="Terminal"),
            patch.object(collector, "_changed_files", return_value=0),
        ):
            collector.sample()
            state = collector.sample()
        self.assertEqual(state.event_kind, "codex_burst")
        self.assertGreater(state.event_strength, 0.8)


class TokenTelemetryTests(unittest.TestCase):
    def test_local_logs_are_reduced_to_deduplicated_token_rates(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        stamp = datetime.fromtimestamp(now - 10, timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex = root / "codex"
            claude = root / "claude"
            codex.mkdir()
            claude.mkdir()
            codex_session = codex / "session.jsonl"
            codex_session.write_text(
                '{"type":"token_usage_record","timestamp":"%s","payload":{"usage":{"total_tokens":600}}}\n'
                % stamp,
                encoding="utf-8",
            )
            claude_record = (
                '{"type":"assistant","timestamp":"%s","message":{"id":"message-1",'
                '"usage":{"input_tokens":60,"output_tokens":20,"cache_read_input_tokens":40}}}'
                % stamp
            )
            (claude / "session.jsonl").write_text(
                f"{claude_record}\n{claude_record}\n",
                encoding="utf-8",
            )
            monitor = LocalTokenMonitor(codex, claude, window_seconds=60)
            sample = monitor.sample(now=now)
            next_stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
            with codex_session.open("a", encoding="utf-8") as handle:
                handle.write(
                    '{"type":"token_usage_record","timestamp":"%s",'
                    '"payload":{"usage":{"total_tokens":600}}}\n' % next_stamp
                )
            changed = monitor.sample(now=now + 1)

        self.assertAlmostEqual(sample.codex_tps, 10.0)
        self.assertAlmostEqual(sample.claude_tps, 2.0)
        self.assertEqual(sample.movement, 0.0)
        self.assertGreater(changed.codex_change, 0.9)


class MusicEngineTests(unittest.TestCase):
    def test_phrase_and_event_variation_add_multiple_musical_roles(self) -> None:
        client = FakeOscClient()
        engine = MusicEngine(client)  # type: ignore[arg-type]
        state = DayState(
            scene="flow",
            focus=0.82,
            energy=0.82,
            urgency=0.55,
            cpu_load=0.48,
            memory_load=0.34,
            ai_activity=0.42,
            codex_tps=900.0,
            claude_tps=700.0,
            open_apps=12,
            git_changes=2,
            active_app="Terminal",
        )
        engine.play_step(0, state)
        first_progression = engine.progression_index
        for step in range(32, 64):
            engine.play_step(step, state)
        event_state = DayState(
            **{
                **state.__dict__,
                "event_serial": 1,
                "event_kind": "codex_burst",
                "event_strength": 0.8,
                "codex_change": 1.0,
                "ai_change": 1.0,
            }
        )
        engine.play_step(64, event_state)

        synths = {message[1][0] for message in client.messages if message[0] == "/s_new"}
        self.assertIn("sonic-pi-prophet", synths)
        self.assertIn("sonic-pi-subpulse", synths)
        self.assertIn("sonic-pi-rhodey", synths)
        self.assertIn("sonic-pi-beep", synths)
        self.assertNotEqual(engine.progression_index, first_progression)
        self.assertGreater(engine.event_steps, 0)


class AudioPathTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "DAYPHONY_SUPERSONIC": "/tmp/example-supersonic",
            "DAYPHONY_SYNTHDEFS": "/tmp/example-synthdefs",
        },
    )
    def test_audio_paths_can_be_configured(self) -> None:
        paths = AudioPaths.discover()
        self.assertEqual(paths.supersonic, Path("/tmp/example-supersonic"))
        self.assertEqual(paths.synthdefs, Path("/tmp/example-synthdefs"))


if __name__ == "__main__":
    unittest.main()
