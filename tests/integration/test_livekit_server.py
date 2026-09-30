"""The real ``livekit.rtc`` against a real server, when there is one.

**TWO tokens are needed, not one.** A LiveKit participant's identity is a claim inside the
JWT, so two participants in one room need two tokens; joining twice with the same one makes
the server see a duplicate identity and disconnect the first. These tests put the SDK and a
stand-in for the robot's edge in the same room, so:

* ``MENLO_SDK_LIVEKIT_TOKEN``: the SDK's, identity ``sdk``
* ``MENLO_SDK_LIVEKIT_EDGE_TOKEN``: the stand-in edge's, identity ``fake-edge``

Run with ``make livekit`` after starting a server::

    livekit-server --dev            # api key devkey / secret secret, ws://127.0.0.1:7880
    uv sync --all-groups
    lk token create --api-key devkey --api-secret secret --join \\
        --room menlo-sdk-it --identity sdk       --valid-for 24h
    lk token create --api-key devkey --api-secret secret --join \\
        --room menlo-sdk-it --identity fake-edge --valid-for 24h
    MENLO_SDK_LIVEKIT_URL=ws://127.0.0.1:7880 \\
    MENLO_SDK_LIVEKIT_TOKEN=<the sdk one> \\
    MENLO_SDK_LIVEKIT_EDGE_TOKEN=<the fake-edge one> uv run pytest -m livekit

These tests skip LOUDLY and cleanly when livekit is not installed, or no server or token
is named. The SDK mints no token (it has no API secret), so the tokens come from the
environment exactly as they come from the robot's manager in production.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from menlo.asimov import ConnectError, Robot
from menlo.asimov._command import Velocity
from menlo.asimov.connection import ConnectionConfig, LiveKitConfig
from menlo.asimov.transport._livekit_client import _LiveKitClient, identity_from_token
from menlo.asimov.transport._wire import COMMAND_TOPIC, STATE_TRACK, encode_command
from menlo.asimov.transport.livekit import LiveKitTransport

pytestmark = pytest.mark.livekit

#: A LiveKit join grant is scoped to ONE room, so every test here uses the same one: the
#: room the two tokens were minted for. Override with MENLO_SDK_LIVEKIT_ROOM.
ROOM = os.environ.get("MENLO_SDK_LIVEKIT_ROOM", "menlo-sdk-it")


@pytest.fixture
def livekit_url() -> str:
    pytest.importorskip("livekit.rtc", reason="livekit is not installed")
    url = os.environ.get("MENLO_SDK_LIVEKIT_URL")
    if not url:
        pytest.skip("MENLO_SDK_LIVEKIT_URL not set: no LiveKit server to join")
    return url


@pytest.fixture
def token() -> str:
    tok = os.environ.get("MENLO_SDK_LIVEKIT_TOKEN")
    if not tok:
        pytest.skip("MENLO_SDK_LIVEKIT_TOKEN not set: the SDK never mints its own token")
    return tok


@pytest.fixture
def edge_token() -> str:
    """A SECOND token, for the stand-in edge. One token cannot carry two participants: the
    identity is a claim inside it, and LiveKit disconnects the earlier duplicate."""
    tok = os.environ.get("MENLO_SDK_LIVEKIT_EDGE_TOKEN")
    if not tok:
        pytest.skip(
            "MENLO_SDK_LIVEKIT_EDGE_TOKEN not set: the stand-in edge needs its own "
            "token (an identity is a claim inside the JWT, so one token is one participant)"
        )
    return tok


def test_the_sdk_joins_a_real_room_and_its_bytes_come_back(livekit_url, token, edge_token):
    """Two clients in one room: the SDK's transport, and a stand-in for the robot's edge.
    What the edge receives on ``commands`` must be exactly what the SDK encoded, and a
    frame on the edge's ``state`` data track must reach ``robot.get_state()`` with its clock."""
    edge = _LiveKitClient(livekit_url, ROOM, token=edge_token)
    received: list[bytes] = []
    edge.connect()
    # The stand-in edge reads commands and publishes state the way the real edge does:
    # data packets in, a data track out. Both on the client's own loop thread.
    rtc = pytest.importorskip("livekit.rtc")
    edge._room.on(
        "data_received",
        lambda p: received.append(bytes(p.data)) if p.topic == COMMAND_TOPIC else None,
    )
    state_track = edge._await(
        edge._room.local_participant.publish_data_track(name=STATE_TRACK), 5.0
    )
    try:
        tx = LiveKitTransport(livekit_url, ROOM, token=token, media_timeout=1.0)
        tx.open()
        try:
            assert tx.identity == identity_from_token(token), "the token says who we are"
            assert tx.identity != edge.identity, "two participants, two identities"
            assert tx.identity is not None and tx.identity in tx.endpoint
            seq = tx.send(Velocity(vx=0.1))
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not received:
                time.sleep(0.05)
            assert received, "the commands topic did not reach the other participant"
            mirror = encode_command(Velocity(vx=0.1), seq)
            from menlo.asimov._proto import load

            a, b = load().command.RobotCommand(), load().command.RobotCommand()
            a.ParseFromString(received[0])
            b.ParseFromString(mirror)
            assert a.sequence == b.sequence == seq
            assert a.policy.vx == pytest.approx(0.1)

            states: list = []
            tx.subscribe_state(states.append)
            stamp = int(time.time() * 1e6)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not states:
                # lossy: a frame pushed before the subscription is up is dropped
                state_track.try_push(
                    rtc.DataTrackFrame(payload=_a_robot_state(), user_timestamp=stamp)
                )
                time.sleep(0.1)
            assert states and len(states[0].joints) == 25
            assert states[0].edge_timestamp_us == stamp
        finally:
            tx.close()
    finally:
        edge.close()


def test_connecting_on_livekit_reports_a_room_that_has_no_robot_in_it(livekit_url, token):
    """A room the SDK can join but no edge answers in is a ConnectError naming the topic,
    never a Robot that looks connected."""
    with pytest.raises(ConnectError) as info:
        Robot(ConnectionConfig(livekit=LiveKitConfig(livekit_url, ROOM, token))).connect(
            "livekit", timeout=2.0, media_timeout=0.5
        )
    assert "state" in str(info.value)


def test_the_speaker_publishes_a_real_audio_track(livekit_url, token):
    client = _LiveKitClient(livekit_url, ROOM, token=token)
    client.connect()
    try:
        from menlo.asimov import AudioChunk

        chunk = AudioChunk(48_000, 1, 480, "pcm_s16le", bytes(960), stream_id="speaker")
        for _ in range(3):
            client.publish_audio(chunk)
    finally:
        client.close()


def test_a_room_with_no_video_track_is_honest_about_it(livekit_url, token):
    tx = LiveKitTransport(livekit_url, ROOM, token=token, media_timeout=1.0)
    tx.open()
    try:
        # Nothing in this suite ever publishes video, so a camera here would be invented.
        assert "camera" not in tx.capabilities
        assert "speaker" in tx.capabilities, "the SDK can always publish into a joined room"
        assert tx.identity is not None, "the token names who we joined as"
    finally:
        tx.close()


def test_the_loop_thread_goes_away_with_close(livekit_url, token):
    """A client that leaked its event-loop thread would keep a process alive after close()."""
    client = _LiveKitClient(livekit_url, ROOM, token=token)
    client.connect()
    assert "menlo-sdk-livekit" in {t.name for t in threading.enumerate()}
    client.close()
    time.sleep(0.5)
    assert "menlo-sdk-livekit" not in {t.name for t in threading.enumerate()}
    client.connect()  # and the same client joins again
    client.close()


def _a_robot_state() -> bytes:
    from menlo.asimov._proto import load

    pb = load()
    msg = pb.state.RobotState(current_mode=pb.common.CONTROL_MODE_DAMP, protocol_version=1)
    msg.joint_pos.extend([0.0] * 25)
    msg.projected_gravity.extend([0.0, 0.0, -1.0])
    msg.sequence = 1
    msg.timestamp_us = int(time.time() * 1e6)
    return bytes(msg.SerializeToString())
