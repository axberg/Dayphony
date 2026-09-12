from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from typing import Iterable


class OscError(RuntimeError):
    pass


def _pad4(data: bytes) -> bytes:
    return data + (b"\0" * ((-len(data)) % 4))


def _string(value: str) -> bytes:
    return _pad4(value.encode("utf-8") + b"\0")


def encode_message(address: str, *arguments: object) -> bytes:
    """Encode the small OSC 1.0 subset needed by scsynth."""
    if not address.startswith("/"):
        raise ValueError("OSC addresses must begin with '/'")

    tags: list[str] = []
    payload: list[bytes] = []
    for value in arguments:
        if isinstance(value, bool):
            tags.append("i")
            payload.append(struct.pack(">i", int(value)))
        elif isinstance(value, int):
            tags.append("i")
            payload.append(struct.pack(">i", value))
        elif isinstance(value, float):
            tags.append("f")
            payload.append(struct.pack(">f", value))
        elif isinstance(value, str):
            tags.append("s")
            payload.append(_string(value))
        else:
            raise TypeError(f"Unsupported OSC argument: {type(value).__name__}")

    return _string(address) + _string("," + "".join(tags)) + b"".join(payload)


def decode_address(packet: bytes) -> str:
    if not packet or packet[0:1] != b"/":
        return ""
    end = packet.find(b"\0")
    if end < 0:
        return ""
    return packet[:end].decode("utf-8", errors="replace")


@dataclass
class OscClient:
    host: str
    port: int

    def __post_init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))

    def send(self, address: str, *arguments: object) -> None:
        self.socket.sendto(encode_message(address, *arguments), (self.host, self.port))

    def wait_for(self, addresses: Iterable[str], timeout: float) -> str:
        wanted = set(addresses)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.socket.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                packet, _ = self.socket.recvfrom(65535)
            except socket.timeout:
                break
            address = decode_address(packet)
            if address == "/fail":
                raise OscError("The audio server rejected an OSC command")
            if address in wanted:
                return address
        raise OscError(f"Timed out waiting for OSC response: {sorted(wanted)}")

    def server_is_ready(self, timeout: float = 0.25) -> bool:
        self.send("/status")
        try:
            self.wait_for(["/status.reply"], timeout)
            return True
        except OscError:
            return False

    def sync(self, sync_id: int, timeout: float = 3.0) -> None:
        self.send("/sync", sync_id)
        self.wait_for(["/synced"], timeout)

    def close(self) -> None:
        self.socket.close()
