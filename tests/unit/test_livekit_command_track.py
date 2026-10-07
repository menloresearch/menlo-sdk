"""Streamed setpoints ride the SDK's own ``commands`` data track; everything else is a packet.

A reliable packet holds back every packet behind it until a lost one is resent, so under
loss a velocity stream stalls. A data-track frame is never held back. So a non-zero velocity
and a trajectory go on the track, when the robot says it reads it, and a mode command or a
zero velocity, which must arrive, goes as a packet. Asimov Edge orders the two by
``RobotCommand.sequence``, so the SDK must hand them out in send order.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from menlo.asimov import LinkLostError
from menlo.asimov._command import ModeCommand, Trajectory, Velocity
from menlo.asimov._proto import load
from menlo.asimov.transport import _livekit_client
from menlo.asimov.transport._livekit_client import _LiveKitClient
from menlo.asimov.transport._wire import (
    COMMAND_TOPIC,
    COMMAND_TRACK,
    COMMAND_TRACK_ATTRIBUTE,
    STATE_TRACK,
    streamed,
)
from tests.conftest import make_livekit_robot, put_in


def _decode(payload: bytes):
    message = load().command.RobotCommand()
    message.ParseFromString(payload)
    return message


@pytest.fixture
def track_robot(edge):
    client, robot = make_livekit_robot(edge, command_track=True)
    robot.open(timeout=3.0)
    yield client, robot
    robot.close()


# ── what is a streamed setpoint ──────────────────────────────────────────────


def test_a_non_zero_velocity_and_a_trajectory_are_streamed_and_nothing_else_is():
    assert streamed(Velocity(vx=0.3)) and streamed(Velocity(vyaw=-0.1))
    assert streamed(Trajectory(positions=(0.0,) * 25))
    assert not streamed(Velocity())  # a hold ending, balance(), close(): must arrive
    assert not streamed(ModeCommand("stand")) and not streamed(ModeCommand("damp"))


# ── the transport's routing ──────────────────────────────────────────────────


def test_a_held_velocity_rides_the_track_and_the_stop_is_a_packet(edge, track_robot):
    client, robot = track_robot
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.3, duration=0.5)
    on_track = [_decode(p) for name, p in client.frames]
    packets = [_decode(p) for topic, p in client.published if topic == COMMAND_TOPIC]
    assert all(name == COMMAND_TRACK for name, _ in client.frames)
    assert len(on_track) >= 3 and all(c.policy.vx == pytest.approx(0.3) for c in on_track)
    # The first velocity only starts the track's publication, so it goes as a packet; the
    # zero that ends the hold must arrive, so it is a packet too.
    assert packets[-1].HasField("policy") and packets[-1].policy.vx == 0.0
    assert next(c.policy.vx for c in packets if c.HasField("policy")) == pytest.approx(0.3)


def test_mode_commands_are_always_packets(edge, track_robot):
    client, robot = track_robot
    robot.stand(wait=False)
    robot.damp(wait=False)
    assert client.frames == []
    modes = [_decode(p).mode for topic, p in client.published if topic == COMMAND_TOPIC]
    assert modes[-2:] == [1, 0]  # STAND, DAMP


def test_a_trajectory_rides_the_track(edge, track_robot):
    client, robot = track_robot
    put_in(robot, edge, "move")
    robot.trajectory([0.05] * 25)  # starts the track; this one goes as a packet
    robot.trajectory([0.06] * 25)
    assert edge.wait_for(lambda rx: sum(c.HasField("all_trajectory") for c in rx) == 2)
    (name, payload), *_ = client.frames
    assert name == COMMAND_TRACK
    assert _decode(payload).all_trajectory.positions[0] == pytest.approx(0.06)


def test_both_lanes_carry_one_sequence_in_send_order(edge, track_robot):
    client, robot = track_robot
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, duration=0.4)
    robot.stand(wait=False)
    assert edge.wait_for(lambda rx: any(c.mode == 1 and not c.HasField("policy") for c in rx))
    sent = sorted(
        [_decode(p) for _n, p in client.frames]
        + [_decode(p) for t, p in client.published if t == COMMAND_TOPIC],
        key=lambda c: c.sequence,
    )
    sequences = [c.sequence for c in sent]
    assert sequences == list(range(sequences[0], sequences[0] + len(sequences)))
    assert sent[-1].mode == 1 and not sent[-1].HasField("policy"), "the STAND is the newest"


def test_a_robot_that_does_not_read_the_track_gets_only_packets(edge):
    client, robot = make_livekit_robot(edge)  # no commands_track attribute
    robot.open(timeout=3.0)
    try:
        put_in(robot, edge, "move")
        robot.set_velocity(vx=0.3, duration=0.4)
        robot.trajectory([0.05] * 25)
    finally:
        robot.close()
    assert client.frames == []
    assert sum(_decode(p).policy.vx > 0 for t, p in client.published if t == COMMAND_TOPIC) >= 3


def test_a_session_that_may_not_publish_a_track_sends_packets(edge):
    client, robot = make_livekit_robot(edge, command_track=True, refuse_track=True)
    robot.open(timeout=3.0)
    try:
        put_in(robot, edge, "move")
        robot.set_velocity(vx=0.3, duration=0.4)
    finally:
        robot.close()
    assert client.frames == []
    assert sum(_decode(p).policy.vx > 0 for t, p in client.published if t == COMMAND_TOPIC) >= 3


# ── the real client: its own track, and the robot's attribute ────────────────


class _Track:
    def __init__(self, fail: bool = False) -> None:
        self.pushed: list[bytes] = []
        self.fail = fail

    def try_push(self, frame) -> None:
        if self.fail:
            raise RuntimeError("frames are being pushed too fast")
        self.pushed.append(frame.payload)


class _Participant:
    def __init__(self, refuse: bool = False) -> None:
        self.published: list[str] = []
        self.track = _Track()
        self.refuse = refuse

    async def publish_data_track(self, *, name: str):
        await asyncio.sleep(0.01)
        if self.refuse:
            raise RuntimeError("Data track publishing unauthorized")
        self.published.append(name)
        return self.track


class _Room:
    def __init__(self, participant: _Participant, roster: dict | None = None) -> None:
        self.local_participant = participant
        self.remote_participants = roster or {}

    async def disconnect(self) -> None:
        pass


@pytest.fixture
def joined(monkeypatch):
    """A real _LiveKitClient on its own loop, with the room replaced by a stub. No livekit:
    the one rtc name push_data_frame uses is stubbed too."""
    monkeypatch.setattr(
        _livekit_client, "_rtc", lambda: SimpleNamespace(DataTrackFrame=SimpleNamespace)
    )
    made: list[_LiveKitClient] = []

    def join(participant: _Participant, roster: dict | None = None) -> _LiveKitClient:
        client = _LiveKitClient("ws://x", "r", token="t")
        client._start_loop()
        client._room = _Room(participant, roster)
        client._connected = True
        made.append(client)
        return client

    yield join
    for client in made:
        client.close()


def _until(predicate, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_the_first_frame_publishes_the_track_and_later_frames_go_out(joined):
    participant = _Participant()
    client = joined(participant)
    assert client.push_data_frame(COMMAND_TRACK, b"one") is False  # publishing now
    assert client.push_data_frame(COMMAND_TRACK, b"two") is False  # still publishing
    assert _until(lambda: participant.published == [COMMAND_TRACK])
    assert client.push_data_frame(COMMAND_TRACK, b"three") is True
    assert participant.track.pushed == [b"three"]
    assert participant.published == [COMMAND_TRACK], "published once, not once per frame"


def test_a_refused_track_is_asked_for_once_and_every_frame_falls_back(joined):
    participant = _Participant(refuse=True)
    client = joined(participant)
    calls = 0
    original = participant.publish_data_track

    async def counting(**kw):
        nonlocal calls
        calls += 1
        return await original(**kw)

    participant.publish_data_track = counting  # type: ignore[method-assign]
    assert client.push_data_frame(COMMAND_TRACK, b"one") is False
    assert _until(lambda: COMMAND_TRACK in client._local_refused)
    for _ in range(5):
        assert client.push_data_frame(COMMAND_TRACK, b"again") is False
    time.sleep(0.05)
    assert calls == 1


def test_a_failed_push_falls_back_instead_of_raising(joined):
    participant = _Participant()
    client = joined(participant)
    client.push_data_frame(COMMAND_TRACK, b"start")
    assert _until(lambda: bool(participant.published))
    participant.track.fail = True
    assert client.push_data_frame(COMMAND_TRACK, b"x") is False


def test_a_failed_packet_surfaces_on_the_next_frame(joined):
    # A stream on the track must not hide that a STAND, a DAMP or a stop never went out.
    participant = _Participant()
    client = joined(participant)
    client.push_data_frame(COMMAND_TRACK, b"start")
    assert _until(lambda: bool(participant.published))
    client._send_error = RuntimeError("the packet was not delivered")
    with pytest.raises(LinkLostError, match="failed"):
        client.push_data_frame(COMMAND_TRACK, b"next")
    assert client.push_data_frame(COMMAND_TRACK, b"after") is True  # reported once


def test_the_sequence_skips_zero_when_it_wraps(edge, track_robot):
    _client, robot = track_robot
    robot._transport._seq = 0xFFFFFFFF
    sent = robot.stand(wait=False)
    assert sent.sequence == 1


def test_pushing_without_a_room_is_link_lost():
    client = _LiveKitClient("ws://x", "r", token="t")
    with pytest.raises(LinkLostError):
        client.push_data_frame(COMMAND_TRACK, b"x")


def test_the_attributes_are_the_state_publishers_and_follow_its_changes(joined):
    robot = SimpleNamespace(identity="MENLO-0001", attributes={COMMAND_TRACK_ATTRIBUTE: "1"})
    other = SimpleNamespace(identity="sdk-other", attributes={COMMAND_TRACK_ATTRIBUTE: "1"})
    client = joined(_Participant(), {"MENLO-0001": robot, "sdk-other": other})
    client.on_data_track(STATE_TRACK, lambda payload, ts: None)
    assert client.publisher_attributes(STATE_TRACK) == {}  # nobody publishes state yet

    def track(name: str, sid: str, identity: str):
        info = SimpleNamespace(name=name, sid=sid)

        class _Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Event().wait()

            async def aclose(self):
                pass

        return SimpleNamespace(
            info=info, publisher_identity=identity, subscribe=lambda **_: _Stream()
        )

    # Another participant's track of another name says nothing about the robot.
    client._on_data_track_published(track("chat", "DTR_c", "sdk-other"))
    assert client.publisher_attributes(STATE_TRACK) == {}
    client._on_data_track_published(track(STATE_TRACK, "DTR_s", "MENLO-0001"))
    assert client.publisher_attributes(STATE_TRACK) == {COMMAND_TRACK_ATTRIBUTE: "1"}
    client._on_attributes_changed({}, SimpleNamespace(identity="MENLO-0001", attributes={}))
    assert client.publisher_attributes(STATE_TRACK) == {}
    client._on_data_track_unpublished("DTR_s")
    assert client.publisher_attributes(STATE_TRACK) == {}


def test_a_robot_that_rejoins_is_asked_again_what_it_reads(joined):
    # The robot restarts with an Asimov Edge that does not read the command track: the
    # attribute remembered from before must not keep frames going to a track nobody reads.
    robot = SimpleNamespace(identity="MENLO-0001", attributes={COMMAND_TRACK_ATTRIBUTE: "1"})
    client = joined(_Participant(), {"MENLO-0001": robot})
    client.on_data_track(STATE_TRACK, lambda payload, ts: None)
    client._on_data_track_published(_state_track("DTR_1", "MENLO-0001"))
    assert client.publisher_attributes(STATE_TRACK) == {COMMAND_TRACK_ATTRIBUTE: "1"}
    client._on_data_track_unpublished("DTR_1")
    robot.attributes = {}  # rejoined, older Asimov Edge
    client._on_data_track_published(_state_track("DTR_2", "MENLO-0001"))
    assert client.publisher_attributes(STATE_TRACK) == {}


def _state_track(sid: str, identity: str):
    class _Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

        async def aclose(self):
            pass

    return SimpleNamespace(
        info=SimpleNamespace(name=STATE_TRACK, sid=sid),
        publisher_identity=identity,
        subscribe=lambda **_: _Stream(),
    )


def test_leaving_the_room_forgets_the_tracks(joined):
    participant = _Participant()
    client = joined(participant)
    client.push_data_frame(COMMAND_TRACK, b"start")
    assert _until(lambda: bool(client._local_tracks))
    client._on_disconnected()
    assert client._local_tracks == {} and client._remote_tracks == {}
