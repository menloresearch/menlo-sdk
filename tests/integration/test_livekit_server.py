"""The real ``livekit.rtc`` against a real server, when there is one.

Run with ``make livekit`` after starting one::

    livekit-server --dev            # api key devkey / secret secret, ws://127.0.0.1:7880
    uv sync --extra livekit
    ASIMOV_SDK_LIVEKIT_URL=ws://127.0.0.1:7880 \\
    ASIMOV_SDK_LIVEKIT_TOKEN=<a token for the room> uv run pytest -m livekit

These tests skip LOUDLY and cleanly when the extra is not installed or no server or token
is named. The SDK mints no token — it has no API secret — so the token comes from the
environment exactly as it comes from the robot's manager in production. Generate one for a
dev server with LiveKit's own CLI: ``lk token create --api-key devkey --api-secret secret
--join --room asimov-sdk-it --identity robot --valid-for 1h``.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from asimov_sdk import ConnectError, Robot
from asimov_sdk._command import Velocity
from asimov_sdk.transport._livekit_client import _LiveKitClient
from asimov_sdk.transport._wire import COMMAND_TOPIC, STATE_TOPIC, encode_command
from asimov_sdk.transport.livekit import LiveKitTransport

pytestmark = pytest.mark.livekit

ROOM = "asimov-sdk-it"


@pytest.fixture
def livekit_url() -> str:
    pytest.importorskip("livekit.rtc", reason="the livekit extra is not installed")
    url = os.environ.get("ASIMOV_SDK_LIVEKIT_URL")
    if not url:
        pytest.skip("ASIMOV_SDK_LIVEKIT_URL not set — no LiveKit server to join")
    return url


@pytest.fixture
def token() -> str:
    tok = os.environ.get("ASIMOV_SDK_LIVEKIT_TOKEN")
    if not tok:
        pytest.skip("ASIMOV_SDK_LIVEKIT_TOKEN not set — the SDK never mints its own token")
    return tok


def test_the_sdk_joins_a_real_room_and_its_bytes_come_back(livekit_url, token):
    """Two clients in one room: the SDK's transport, and a stand-in for the robot's edge.
    What the edge receives on ``commands`` must be exactly what the SDK encoded, and a
    ``state`` packet from the edge must reach ``robot.state``."""
    edge = _LiveKitClient(livekit_url, ROOM, token=token, identity="fake-edge")
    received: list[bytes] = []
    edge.on_data(COMMAND_TOPIC, received.append)
    edge.connect()
    try:
        tx = LiveKitTransport(livekit_url, ROOM, token=token, identity="sdk", media_timeout=1.0)
        tx.open()
        try:
            seq = tx.send(Velocity(vx=0.1))
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not received:
                time.sleep(0.05)
            assert received, "the commands topic did not reach the other participant"
            mirror = encode_command(Velocity(vx=0.1), seq)
            from asimov_sdk._proto import load

            a, b = load().command.RobotCommand(), load().command.RobotCommand()
            a.ParseFromString(received[0])
            b.ParseFromString(mirror)
            assert a.sequence == b.sequence == seq
            assert a.policy.vx == pytest.approx(0.1)

            states: list = []
            tx.subscribe_state(states.append)
            edge.publish_data(_a_robot_state(), topic=STATE_TOPIC)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not states:
                time.sleep(0.05)
            assert states and len(states[0].joints) == 25
        finally:
            tx.close()
    finally:
        edge.close()


def test_connect_livekit_reports_a_room_that_has_no_robot_in_it(livekit_url, token):
    """A room the SDK can join but no edge answers in is a ConnectError naming the topic —
    never a Robot that looks connected."""
    with pytest.raises(ConnectError) as info:
        Robot.connect_livekit(
            livekit_url, f"{ROOM}-empty", token=token, timeout=2.0, media_timeout=0.5
        )
    assert "state" in str(info.value)


def test_the_speaker_publishes_a_real_audio_track(livekit_url, token):
    client = _LiveKitClient(livekit_url, f"{ROOM}-audio", token=token, identity="sdk-audio")
    client.connect()
    try:
        from asimov_sdk import AudioChunk

        chunk = AudioChunk(48_000, 1, 480, "pcm_s16le", bytes(960), stream_id="speaker")
        for _ in range(3):
            client.publish_audio(chunk)
    finally:
        client.close()


def test_a_room_with_no_video_track_is_honest_about_it(livekit_url, token):
    tx = LiveKitTransport(
        livekit_url, f"{ROOM}-silent", token=token, identity="sdk-quiet", media_timeout=1.0
    )
    tx.open()
    try:
        assert "camera" not in tx.capabilities and "microphone" not in tx.capabilities
        assert "speaker" in tx.capabilities, "the SDK can always publish into a joined room"
    finally:
        tx.close()


def test_the_loop_thread_goes_away_with_close(livekit_url, token):
    """A client that leaked its event-loop thread would keep a process alive after close()."""
    client = _LiveKitClient(livekit_url, f"{ROOM}-threads", token=token, identity="sdk-threads")
    client.connect()
    assert "asimov-sdk-livekit" in {t.name for t in threading.enumerate()}
    client.close()
    time.sleep(0.5)
    assert "asimov-sdk-livekit" not in {t.name for t in threading.enumerate()}
    client.connect()  # and the same client joins again
    client.close()


def _a_robot_state() -> bytes:
    from asimov_sdk._proto import load

    pb = load()
    msg = pb.state.RobotState(current_mode=pb.common.CONTROL_MODE_DAMP, protocol_version=1)
    msg.joint_pos.extend([0.0] * 25)
    msg.projected_gravity.extend([0.0, 0.0, -1.0])
    msg.sequence = 1
    msg.timestamp_us = int(time.time() * 1e6)
    return bytes(msg.SerializeToString())
