"""Shared fixtures: a fake edge that speaks the real wire, and a live-rig gate."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable

import pytest

from asimov_sdk import Robot


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeEdge:
    """The edge's UDP lane, as the SDK sees it: bare ``RobotCommand`` in, bare
    ``RobotState`` pushed out to a configured host at a configured rate.

    Deliberately shaped like ``asimov-edge``'s ``UdpConnector`` — sender address ignored,
    state to ONE destination — so a test that passes here says something about the robot.
    Records every decoded command; the state it pushes is whatever the test sets.
    """

    def __init__(self, *, state_hz: float = 100.0) -> None:
        from asimov_protocol.v1 import asimov_command_pb2 as cmd_pb
        from asimov_protocol.v1 import asimov_common_pb2 as common_pb
        from asimov_protocol.v1 import asimov_state_pb2 as st_pb

        self._cmd_pb, self._common_pb, self._st_pb = cmd_pb, common_pb, st_pb
        self.command_port = _free_port()
        self.state_port = _free_port()
        self.received: list = []  # decoded RobotCommand protos, in arrival order
        self.state = st_pb.RobotState(current_mode=common_pb.CONTROL_MODE_DAMP, protocol_version=1)
        self.state.joint_pos.extend([0.0] * 25)
        self.state.projected_gravity.extend([0.0, 0.0, -1.0])
        self.pushing = True
        self._hz = state_hz
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", self.command_port))
        self._sock.settimeout(0.05)
        self._rx = threading.Thread(target=self._recv, daemon=True)
        self._tx = threading.Thread(target=self._push, daemon=True)
        self._rx.start()
        self._tx.start()

    def _recv(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            c = self._cmd_pb.RobotCommand()
            c.ParseFromString(data)
            self.received.append(c)

    def _push(self) -> None:
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        period = 1.0 / self._hz
        seq = 0
        while not self._stop.is_set():
            if self.pushing:
                seq += 1
                self.state.sequence = seq
                self.state.timestamp_us = int(time.time() * 1e6)
                out.sendto(self.state.SerializeToString(), ("127.0.0.1", self.state_port))
            time.sleep(period)
        out.close()

    # ── test helpers ─────────────────────────────────────────────────────────
    def set_mode(self, mode: str) -> None:
        self.state.current_mode = getattr(self._common_pb, f"CONTROL_MODE_{mode.upper()}")

    def velocities(self) -> list[tuple[float, float, float]]:
        return [
            (round(c.policy.vx, 4), round(c.policy.vy, 4), round(c.policy.vyaw, 4))
            for c in self.received
            if c.HasField("policy")
        ]

    def modes(self) -> list[str]:
        names = {0: "damp", 1: "stand", 2: "move"}
        return [names[c.mode] for c in self.received if not c.HasField("policy")]

    def wait_for(self, predicate: Callable[[list], bool], timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.received):
                return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        self._stop.set()
        self._rx.join(timeout=1.0)
        self._tx.join(timeout=1.0)
        self._sock.close()


@pytest.fixture
def edge():
    e = FakeEdge()
    yield e
    e.close()


@pytest.fixture
def robot(edge):
    r = Robot.connect_direct(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=3.0,
    )
    yield r
    r.close()


@pytest.fixture
def live_host() -> str:
    """A real robot or studio rig, named by ASIMOV_SDK_LIVE_HOST. Skips LOUDLY otherwise."""
    host = os.environ.get("ASIMOV_SDK_LIVE_HOST")
    if not host:
        pytest.skip("ASIMOV_SDK_LIVE_HOST not set — no live robot to drive")
    return host
