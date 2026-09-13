from __future__ import annotations

from typing import Literal

from mcp.server import MCPServer

from . import __version__
from .control import ControlError, send_control_request


mcp = MCPServer(
    "Dayphony",
    version=__version__,
    instructions=(
        "Use request_attention only when the human needs to provide input, approval, "
        "or help unblock work. Dayphony turns the typed event into a local musical cue."
    ),
)


@mcp.tool()
def request_attention(
    agent: Literal["codex", "claude", "other"],
    reason: Literal["input_needed", "approval_needed", "blocked", "error"] = "input_needed",
    priority: float = 0.7,
) -> dict[str, object]:
    """Play a musical attention cue when an agent genuinely needs the human.

    `priority` ranges from 0 (subtle) to 1 (urgent). Do not call this for routine
    progress updates. No prompt, response, task text, or tool arguments are sent.
    """

    response = send_control_request(
        {
            "method": "notify",
            "agent": agent,
            "reason": reason,
            "priority": max(0.0, min(1.0, priority)),
        }
    )
    if not response.get("ok"):
        raise ControlError(str(response.get("error", "Dayphony rejected the signal")))
    return response


@mcp.tool()
def soundtrack_status() -> dict[str, object]:
    """Return Dayphony's local aggregate state without exposing private content."""

    response = send_control_request({"method": "status"})
    if not response.get("ok"):
        raise ControlError(str(response.get("error", "Dayphony status is unavailable")))
    return response


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
