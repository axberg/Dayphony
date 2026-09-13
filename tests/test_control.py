from __future__ import annotations

import asyncio
import stat
import tempfile
import unittest
from pathlib import Path
from queue import Queue

from dayphony.control import AgentEvent, ControlServer, send_control_request


class ControlServerTests(unittest.TestCase):
    def test_status_and_attention_signal_stay_on_private_socket(self) -> None:
        events: Queue[AgentEvent] = Queue()
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "control.sock"
            server = ControlServer(events, endpoint)
            server.start()
            try:
                server.update_status({"scene": "flow", "energy": 0.72})
                status_response = send_control_request({"method": "status"}, endpoint)
                notify_response = send_control_request(
                    {
                        "method": "notify",
                        "agent": "codex",
                        "reason": "input_needed",
                        "priority": 0.9,
                    },
                    endpoint,
                )
                permissions = stat.S_IMODE(endpoint.stat().st_mode)
            finally:
                server.close()

        self.assertTrue(status_response["ok"])
        self.assertEqual(status_response["scene"], "flow")
        self.assertTrue(notify_response["ok"])
        self.assertEqual(events.get_nowait(), AgentEvent("codex", "input_needed", 0.9))
        self.assertEqual(permissions, 0o600)
        self.assertFalse(endpoint.exists())

    def test_invalid_notification_is_rejected(self) -> None:
        events: Queue[AgentEvent] = Queue()
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "control.sock"
            server = ControlServer(events, endpoint)
            server.start()
            try:
                response = send_control_request(
                    {"method": "notify", "agent": "unknown", "reason": "input_needed"},
                    endpoint,
                )
            finally:
                server.close()
        self.assertFalse(response["ok"])
        self.assertTrue(events.empty())


try:
    from mcp import Client

    from dayphony.mcp_server import mcp
except ImportError:
    Client = None  # type: ignore[assignment,misc]
    mcp = None


@unittest.skipIf(Client is None, "install dayphony[mcp] to test the MCP adapter")
class McpServerTests(unittest.TestCase):
    def test_exposes_attention_and_status_tools(self) -> None:
        async def list_tool_names() -> list[str]:
            assert Client is not None
            assert mcp is not None
            async with Client(mcp) as client:
                result = await client.list_tools()
                return [tool.name for tool in result.tools]

        names = asyncio.run(list_tool_names())
        self.assertEqual(names, ["request_attention", "soundtrack_status"])


if __name__ == "__main__":
    unittest.main()
