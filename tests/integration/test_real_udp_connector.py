"""Drive asimov-edge's REAL `UdpConnector`, in-process, with the SDK's transport.

The fake edge in conftest is shaped like the connector; this proves the shape. It needs
an asimov-edge checkout on the path (``ASIMOV_EDGE_SRC``, pinned in ``edge.pin``), so it
is marked ``integration`` and skips — loudly, by name — when that is missing.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import time

import pytest

from asimov_sdk import Velocity
from asimov_sdk.transport.udp import UdpTransport

pytestmark = pytest.mark.integration

EDGE_SRC = os.environ.get("ASIMOV_EDGE_SRC")
if not EDGE_SRC:
    pytest.skip("ASIMOV_EDGE_SRC not set — cannot import asimov-edge", allow_module_level=True)
sys.path.insert(0, EDGE_SRC)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_the_edge_admits_our_velocity_as_a_udp_source():
    from edge.connectors.udp_connector import UdpConnector
    from edge.control_source import ControlSource
    from edge.topic_registry import TopicRegistry
    from edge.topics import COMMAND_TOPIC

    cmd_port, state_port = _free_port(), _free_port()
    topics = TopicRegistry()
    sub = topics.subscribe(COMMAND_TOPIC)
    conn = UdpConnector(
        topics,
        command_host="127.0.0.1",
        command_port=cmd_port,
        state_host="127.0.0.1",
        state_port=state_port,
    )

    async def main():
        await conn.start()
        tx = UdpTransport("127.0.0.1", command_port=cmd_port, state_bind=("127.0.0.1", state_port))
        tx.open()
        seq = tx.send(Velocity(vx=0.42))
        got = None
        for _ in range(100):
            try:
                got = sub.get_nowait()
                break
            except Exception:
                await asyncio.sleep(0.02)
        tx.close()
        await conn.stop()
        return seq, got

    seq, got = asyncio.run(main())
    assert got is not None, "the connector published nothing to the arbiter's command topic"
    assert got.source is ControlSource.UDP
    assert got.command.policy.vx == pytest.approx(0.42)
    assert got.command.sequence == seq
    assert conn.connected is False  # stopped


def test_the_edge_forwards_robot_state_to_our_bound_port():
    from asimov_protocol.v1 import asimov_state_pb2 as st_pb
    from edge.connectors.udp_connector import UdpConnector
    from edge.messages import RobotStateMessage
    from edge.topic_registry import TopicRegistry
    from edge.topics import STATE_TOPIC

    cmd_port, state_port = _free_port(), _free_port()
    topics = TopicRegistry()
    conn = UdpConnector(
        topics,
        command_host="127.0.0.1",
        command_port=cmd_port,
        state_host="127.0.0.1",
        state_port=state_port,
    )
    seen = []

    async def main():
        await conn.start()
        tx = UdpTransport("127.0.0.1", command_port=cmd_port, state_bind=("127.0.0.1", state_port))
        tx.subscribe_state(seen.append)
        tx.open()
        msg = st_pb.RobotState(current_mode=1, protocol_version=1)
        msg.joint_pos.extend([0.5] * 25)
        topics.publish(STATE_TOPIC, RobotStateMessage(msg))
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        tx.close()
        await conn.stop()

    asyncio.run(main())
    assert seen, "no RobotState reached the SDK's state port"
    assert seen[0].mode.name == "STAND" and seen[0].joint("L_Knee").pos == pytest.approx(0.5)
