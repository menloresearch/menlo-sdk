"""The LiveKit lanes against a faked client seam: the wire contract, the capability
honesty, the media API — and the promise that none of it is needed to drive a robot.

Nothing here imports livekit. That is the point: every ``livekit`` import in the SDK sits
behind ``transport/_livekit_client.py``, so the suite fakes that seam and the tests run on
a machine that has never installed the extra.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
import wave

import pytest

from menlo.asimov import (
    AudioChunk,
    ConnectError,
    Frame,
    LinkLostError,
    Mode,
    NotConnectedError,
    ProtocolMismatchError,
    Robot,
    UnsupportedError,
    WaitTimeoutError,
)
from menlo.asimov._command import Velocity
from menlo.asimov._media import Clip
from menlo.asimov.transport._livekit_client import (
    _LiveKitClient,
)
from menlo.asimov.transport._wire import COMMAND_TOPIC, STATE_TRACK, encode_command
from menlo.asimov.transport.livekit import LiveKitTransport
from tests.conftest import FakeLiveKitClient, make_livekit_robot

# ── the wire contract ─────────────────────────────────────────────────────────


def test_a_command_is_one_bare_RobotCommand_on_the_commands_topic(edge, livekit_robot):
    client, robot = livekit_robot
    sent = robot.stand()
    assert edge.wait_for(lambda rx: any(c.mode == 1 for c in rx))
    topic, payload = next(p for p in client.published if p[0] == COMMAND_TOPIC)
    assert topic == "commands", "the topic identifies the type; there is no envelope"
    from menlo.asimov._proto import load

    decoded = load().command.RobotCommand()
    decoded.ParseFromString(payload)
    assert decoded.mode == 1 and decoded.sequence == sent.sequence
    assert not decoded.HasField("policy")


def test_the_livekit_payload_is_byte_identical_to_the_udp_one(edge, livekit_robot):
    """The edge parses ONE message either way. A packet that differed by so much as a
    field would make the room a second protocol to maintain."""
    client, robot = livekit_robot
    sent = robot.set_velocity(vx=0.2)
    _topic, payload = client.published[-1]
    mirror = encode_command(Velocity(0.2, 0.0, 0.0), sent.sequence)
    from menlo.asimov._proto import load

    a, b = load().command.RobotCommand(), load().command.RobotCommand()
    a.ParseFromString(payload)
    b.ParseFromString(mirror)
    a.timestamp_us = b.timestamp_us = 0  # the only field that is a clock, not a decision
    assert a.SerializeToString() == b.SerializeToString()


def test_state_is_the_same_bytes_the_udp_lane_echoes(livekit_robot):
    _client, robot = livekit_robot
    assert robot.info.dof == 25
    assert robot.info.transport == "livekit"
    assert robot.info.joint_names is not None and robot.info.joint_names[3] == "L_Knee"
    assert robot.state.upright is True


def test_an_undecodable_state_packet_is_dropped_not_fatal(livekit_robot):
    client, robot = livekit_robot
    before = robot.state.sequence
    for cb in client._data_track_cbs[STATE_TRACK]:
        cb(b"\xff\xff\xff\xff not a protobuf", None)
    time.sleep(0.05)
    assert robot.state.sequence >= before and robot.connected


# ── capability honesty ────────────────────────────────────────────────────────


def test_capabilities_are_the_tracks_that_actually_arrived(edge):
    client, robot = make_livekit_robot(edge, tracks=("camera",))
    robot.open(timeout=3.0)
    try:
        assert robot.has("camera") and robot.has("speaker")
        assert not robot.has("microphone"), "no audio track arrived; do not claim one"
        assert client.tracks == frozenset({"camera"})
    finally:
        robot.close()


def test_a_room_with_no_video_raises_unsupported_instead_of_hanging(edge):
    _client, robot = make_livekit_robot(edge, tracks=())
    robot.open(timeout=3.0)
    try:
        assert not robot.has("camera") and not robot.has("microphone")
        with pytest.raises(UnsupportedError) as info:
            robot.camera.photo(timeout=0.1)
        assert info.value.capability == "camera" and "livekit" in str(info.value)
        with pytest.raises(UnsupportedError):
            robot.microphone.latest()
    finally:
        robot.close()


def test_a_closed_room_stops_claiming_media(edge):
    client, robot = make_livekit_robot(edge)
    robot.open(timeout=3.0)
    assert "camera" in robot._tx.capabilities
    robot.close()
    assert "camera" not in robot._tx.capabilities and not client.connected


# ── the camera API ────────────────────────────────────────────────────────────


def test_frames_are_rgb8_so_to_numpy_would_work(livekit_robot):
    client, robot = livekit_robot
    threading.Timer(0.02, client.push_frame, args=(1,)).start()
    frame = robot.camera.photo(timeout=1.0)
    assert frame.encoding == "rgb8", "yuv420 would make Frame.to_numpy() refuse"
    assert frame.shape == (2, 4) and frame.stride_bytes == 12


def test_photo_waits_for_a_fresh_frame_and_times_out_honestly(livekit_robot):
    client, robot = livekit_robot
    stale = client.push_frame(1)
    threading.Timer(0.05, client.push_frame, args=(2,)).start()
    fresh = robot.camera.photo(timeout=1.0)
    assert fresh.sequence == 2 and fresh is not stale, "photo() must not return a cached frame"
    with pytest.raises(WaitTimeoutError):
        robot.camera.photo(timeout=0.1)  # nobody is pushing any more


def test_capture_clip_records_both_streams_and_writes_a_wav(livekit_robot, tmp_path):
    client, robot = livekit_robot
    robot.camera.latest()  # attach before the feeder starts
    robot.microphone.latest()
    stop = threading.Event()

    def feed() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            client.push_frame(n)
            client.push_audio(n)
            time.sleep(0.01)

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    try:
        clip = robot.camera.capture_clip(0.2)
    finally:
        stop.set()
        thread.join(timeout=1.0)
    assert isinstance(clip, Clip) and clip.frames and clip.audio
    assert clip.fps > 0 and clip.duration_s > 0
    out = clip.save_wav(tmp_path / "clip.wav")
    with wave.open(str(out), "rb") as wav:
        assert wav.getframerate() == 16_000 and wav.getnchannels() == 1
        assert wav.getnframes() == sum(c.samples_per_channel for c in clip.audio)


def test_capture_clip_refuses_the_audio_the_room_does_not_carry(edge):
    _client, robot = make_livekit_robot(edge, tracks=("camera",))
    robot.open(timeout=3.0)
    try:
        with pytest.raises(UnsupportedError) as info:
            robot.camera.capture_clip(0.05)
        assert info.value.capability == "microphone"
    finally:
        robot.close()


def test_an_empty_clip_is_a_dead_camera_not_a_short_recording(livekit_robot):
    _client, robot = livekit_robot
    with pytest.raises(WaitTimeoutError):
        robot.camera.capture_clip(0.05, audio=False)


def test_clip_exports_that_need_an_imaging_library_say_so(tmp_path):
    """No new hard dependency: JPEG, MP4 and numpy exports name the package to install."""
    clip = Clip(
        frames=(Frame(width=2, height=1, encoding="rgb8", data=bytes(6), stride_bytes=6),),
        audio=(),
        started_at=time.time(),
    )
    for module, call in (
        ("PIL", lambda: clip.save_frames(tmp_path)),
        ("cv2", lambda: clip.save_mp4(tmp_path / "c.mp4")),
        ("numpy", clip.frames_as_numpy),
    ):
        if importlib.util.find_spec(module) is not None:
            continue  # it IS installed here; the export works and has nothing to say
        with pytest.raises(ImportError) as info:
            call()
        assert "pip install" in str(info.value)
    with pytest.raises(ValueError):
        clip.save_wav(tmp_path / "c.wav")  # no audio


def test_save_wav_refuses_encoded_audio(tmp_path):
    clip = Clip(
        frames=(),
        audio=(AudioChunk(48_000, 1, 960, "opus", b"\x00" * 10),),
        started_at=time.time(),
    )
    with pytest.raises(ValueError) as info:
        clip.save_wav(tmp_path / "c.wav")
    assert "pcm_s16le" in str(info.value)


def test_the_speaker_publishes_onto_the_room(livekit_robot):
    client, robot = livekit_robot
    robot.speaker.play_pcm(bytes(3200), sample_rate_hz=16_000)
    assert len(client.played) == 1 and client.played[0].samples_per_channel == 1600
    assert client.played[0].encoding == "pcm_s16le"


# ── media without state ───────────────────────────────────────────────────────


def test_a_media_only_session_opens_without_state_and_refuses_to_drive(edge):
    """The camera, microphone and speaker are the room's; the firmware need not be up for
    them. A session that does not wait for state gets the media at once, and every verb
    that would move the robot refuses until the robot has reported."""
    client = FakeLiveKitClient(edge, carry_state=False)  # no state track in this room
    tx = LiveKitTransport("ws://fake", "asimov-room", token="t", client=client)
    robot = Robot(tx, link_timeout=0.3)
    started = time.monotonic()
    robot.open(timeout=3.0, require_state=False)
    try:
        assert time.monotonic() - started < 1.0, "a media-only open must not wait for state"
        assert robot.connected and robot.has("camera") and robot.has("speaker")
        threading.Timer(0.02, client.push_frame, args=(1,)).start()
        assert robot.camera.photo(timeout=1.0).sequence == 1
        robot.speaker.play_pcm(bytes(320))
        assert len(client.played) == 1
        with pytest.raises(NotConnectedError, match="has not reported state"):
            _ = robot.state
        with pytest.raises(NotConnectedError, match="has not reported state"):
            _ = robot.info
        for verb in (
            robot.stand,
            robot.damp,
            robot.stop,
            lambda: robot.set_velocity(vx=0.1),
            lambda: robot.trajectory([0.0] * 25),
        ):
            with pytest.raises(NotConnectedError, match="has not reported state"):
                verb()
        with pytest.raises(WaitTimeoutError):
            robot.wait_for(Mode.STAND, timeout=0.2)
        time.sleep(0.8)  # well past link_timeout
        assert robot.connected, "a stream that never started is not one that went quiet"
        assert [t for t in client.published if t[0] == COMMAND_TOPIC] == [], "nothing went out"
    finally:
        robot.close()


def test_state_arriving_later_completes_the_handshake_and_unlocks_the_verbs(edge):
    edge.pushing = False  # the firmware is down when the script starts
    _client, robot = make_livekit_robot(edge)
    robot.open(timeout=0.2, require_state=False)
    try:
        with pytest.raises(NotConnectedError):
            robot.stand()
        edge.pushing = True  # and comes up mid-session
        assert robot.wait_until(lambda s: s.mode is Mode.DAMP, timeout=3.0).mode is Mode.DAMP
        assert robot.info.dof == 25 and robot.info.joint_names is not None
        assert robot.state.upright is True
        robot.stand()
        assert edge.wait_for(lambda rx: edge.modes().count("stand") == 1)
    finally:
        robot.close()


def test_a_late_protocol_mismatch_is_raised_where_it_is_read_not_lost_in_a_log(edge):
    edge.pushing = False
    edge.state.protocol_version = 99
    _client, robot = make_livekit_robot(edge)
    robot.open(timeout=0.2, require_state=False)
    try:
        edge.pushing = True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and robot._handshake_error is None:
            time.sleep(0.02)
        with pytest.raises(ProtocolMismatchError) as info:
            _ = robot.state
        assert info.value.observed == 99
        with pytest.raises(ProtocolMismatchError):
            robot.stand()
        with pytest.raises(ProtocolMismatchError):
            robot.wait_for(Mode.STAND, timeout=1.0)
        assert robot.connected, "the room is fine; it is the robot this SDK cannot talk to"
    finally:
        robot.close()


def test_a_session_that_waited_for_state_still_fails_loudly_without_it(edge):
    edge.pushing = False
    _client, robot = make_livekit_robot(edge)
    with pytest.raises(ConnectError, match="no state from the robot"):
        robot.open(timeout=0.3)


# ── lifecycle ─────────────────────────────────────────────────────────────────


def test_the_transport_refuses_to_open_twice(edge):
    tx = LiveKitTransport("ws://fake", "r", token="t", client=FakeLiveKitClient(edge))
    tx.open()
    try:
        with pytest.raises(ConnectError):
            tx.open()
    finally:
        tx.close()


def test_close_never_raises_and_the_transport_reopens(edge):
    client = FakeLiveKitClient(edge)
    tx = LiveKitTransport("ws://fake", "r", token="t", client=client)
    tx.close()  # never opened
    robot = Robot(tx)
    robot.open(timeout=3.0)
    first = robot.state.sequence
    robot.close()
    robot.open(timeout=3.0)  # the same transport, a second session
    try:
        assert robot.state.sequence >= first
        assert len(client._data_track_cbs[STATE_TRACK]) == 1, "the state track was wired twice"
    finally:
        robot.close()


def test_only_the_named_data_track_is_read_and_its_frames_carry_the_edge_clock():
    """A room may carry other participants' data tracks; only the robot's ``state`` track
    feeds the state callbacks, and each frame's user_timestamp comes through."""
    client = _LiveKitClient("ws://x", "r", token="t")
    seen: list[tuple[bytes, int | None]] = []
    client.on_data_track(STATE_TRACK, lambda payload, ts: seen.append((payload, ts)))

    class Frame:
        def __init__(self, payload, ts):
            self.payload, self.user_timestamp = payload, ts

    class Stream:
        def __init__(self, frames):
            self._frames = list(frames)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._frames:
                raise StopAsyncIteration
            return self._frames.pop(0)

        async def aclose(self):
            pass

    class Track:
        def __init__(self, name, frames):
            self.info = type("Info", (), {"name": name, "sid": f"DTR_{name}"})()
            self._frames = frames

        def subscribe(self, **_):
            return Stream(self._frames)

    async def run():
        await client._pump_data_track(
            Track(STATE_TRACK, [Frame(b"one", 1000), Frame(b"two", None)]), STATE_TRACK
        )

    asyncio.run(run())
    assert seen == [(b"one", 1000), (b"two", None)]
    # a track with another name is not the state track: no reader is started for it
    client._on_data_track_published(Track("chat", [Frame(b"hi", None)]))
    assert client._data_track_readers == {}


def test_publishing_without_a_room_is_link_lost_not_an_attribute_error():
    client = _LiveKitClient("ws://x", "r", token="t")
    with pytest.raises(LinkLostError):
        client.publish_data(b"x", topic=COMMAND_TOPIC)
    with pytest.raises(LinkLostError):
        client.publish_audio(AudioChunk(16_000, 1, 160, "pcm_s16le", bytes(320)))
    with pytest.raises(ValueError):
        client.publish_audio(AudioChunk(48_000, 1, 960, "opus", bytes(10)))
    client.close()  # idempotent, never raises, even having never connected


def test_bgr8_to_rgb_swaps_channels_without_flipping_the_image() -> None:
    """`data[::-1]` reverses the whole buffer: right channels, mirrored upside-down image.

    A channel swap has to reorder bytes *within* each pixel, so pixel and row order survive.
    """
    from menlo.asimov._media import _rgb_bytes

    # Four distinguishable BGR pixels in a 2x2 frame.
    bgr = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    frame = Frame(width=2, height=2, encoding="bgr8", data=bgr)
    assert _rgb_bytes(frame) == bytes([3, 2, 1, 6, 5, 4, 9, 8, 7, 12, 11, 10])

    # rgb8 is handed back untouched.
    assert _rgb_bytes(Frame(width=2, height=2, encoding="rgb8", data=bgr)) == bgr
