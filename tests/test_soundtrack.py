from __future__ import annotations

import struct
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from dayphony.context import ContextCollector, PRESETS, with_energy
from dayphony.engine import AudioPaths
from dayphony.osc import decode_address, encode_message


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
        collector = ContextCollector(Path.cwd())
        state = collector.sample()
        self.assertIn(state.scene, ("focus", "flow"))
        self.assertGreater(state.focus, 0.8)
        self.assertFalse(state.meeting_active)


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
