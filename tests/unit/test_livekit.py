"""The LiveKit lanes against a faked client seam: the wire contract, the capability
honesty, the media API — and the promise that none of it is needed to drive a robot.

Nothing here imports livekit. That is the point: every ``livekit`` import in the SDK sits
behind ``transport/_livekit_client.py``, so the suite fakes that seam and the tests run on
a machine that has never installed the extra.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
import wave

import pytest

from asimov_sdk import (
    AudioChunk,
    ConnectError,
    Frame,
    LinkLostError,
    NotConnectedError,
    Robot,
    UnsupportedError,
    WaitTimeoutError,
)
from asimov_sdk._command import Velocity
from asimov_sdk._media import Clip
from asimov_sdk.transport._livekit_client import (
    _LiveKitClient,
    _rtc,
    identity_from_token,
)
from asimov_sdk.transport._wire import COMMAND_TOPIC, STATE_TOPIC, encode_command
from asimov_sdk.transport.livekit import HybridTransport, LiveKitTransport
from tests.conftest import FakeLiveKitClient, make_livekit_robot

# ── the wire contract ─────────────────────────────────────────────────────────


def test_a_command_is_one_bare_RobotCommand_on_the_commands_topic(edge, livekit_robot):
    client, robot = livekit_robot
    sent = robot.stand()
    assert edge.wait_for(lambda rx: any(c.mode == 1 for c in rx))
    topic, payload = next(p for p in client.published if p[0] == COMMAND_TOPIC)
    assert topic == "commands", "the topic identifies the type; there is no envelope"
    from asimov_sdk._proto import load

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
    from asimov_sdk._proto import load

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
    for cb in client._data_cbs[STATE_TOPIC]:
        cb(b"\xff\xff\xff\xff not a protobuf")
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
        assert len(client._data_cbs[STATE_TOPIC]) == 1, "the state topic was wired twice"
    finally:
        robot.close()


def test_send_before_open_is_not_connected(edge):
    tx = LiveKitTransport("ws://fake", "r", token="t", client=FakeLiveKitClient(edge))
    with pytest.raises(NotConnectedError):
        tx.send(Velocity(vx=0.1))


def test_publishing_into_a_room_that_is_gone_is_link_lost(edge):
    client = FakeLiveKitClient(edge)
    tx = LiveKitTransport("ws://fake", "r", token="t", client=client)
    tx.open()
    client.connected = False  # the SFU dropped us
    with pytest.raises(LinkLostError):
        tx.send(Velocity(vx=0.1))
    tx.close()


def test_the_endpoint_is_readable(edge):
    tx = LiveKitTransport("ws://sfu.local", "asimov-42", token="t", client=FakeLiveKitClient(edge))
    assert tx.endpoint == "asimov-42@ws://sfu.local" and tx.kind == "livekit"


# ── the hybrid lane ───────────────────────────────────────────────────────────


def test_hybrid_drives_on_udp_and_watches_on_the_room(edge, hybrid_robot):
    client, robot = hybrid_robot
    assert robot.info.transport == "hybrid"
    assert robot.info.capabilities == frozenset(
        {"drive", "state", "camera", "microphone", "speaker"}
    )
    robot.stand()
    assert edge.wait_for(lambda rx: any(c.mode == 1 for c in rx))
    assert client.published == [], "control must stay on the direct lane in hybrid mode"
    threading.Timer(0.02, client.push_frame, args=(1,)).start()
    assert robot.camera.photo(timeout=1.0).encoding == "rgb8"


def test_hybrid_closes_the_udp_half_when_the_room_will_not_join(edge):
    class Refusing(FakeLiveKitClient):
        def connect(self) -> None:
            raise ConnectError("no token")

    tx = HybridTransport(
        "127.0.0.1",
        livekit_url="ws://fake",
        room="r",
        token="t",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        client=Refusing(edge, carry_state=False),
    )
    with pytest.raises(ConnectError):
        tx.open()
    assert tx._udp._sock is None, "a failed join must not leave a socket and a reader behind"
    tx.close()  # never raises, having never opened


def test_hybrid_media_dying_leaves_the_robot_driveable(edge, hybrid_robot):
    client, robot = hybrid_robot
    client.close()  # the room dropped; the UDP lane did not
    robot.stand()
    assert edge.wait_for(lambda rx: any(c.mode == 1 for c in rx))
    assert robot.connected
    with pytest.raises(UnsupportedError):
        robot.camera.photo(timeout=0.1)


# ── LiveKit stays optional ────────────────────────────────────────────────────


def test_the_sdk_drives_a_robot_with_livekit_unavailable(edge, monkeypatch, robot):
    """The hard requirement: `pip install asimov-sdk` with NO extra drives a robot."""

    class Blocked:
        def find_module(self, name, path=None):
            return None

        def find_spec(self, name, path=None, target=None):
            if name == "livekit" or name.startswith("livekit."):
                raise ImportError("No module named 'livekit'")
            return None

    monkeypatch.setattr(sys, "meta_path", [Blocked(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, "livekit", raising=False)
    robot.stand()
    assert edge.wait_for(lambda rx: any(c.mode == 1 for c in rx)), "mode A0 needs no livekit"
    with pytest.raises(ConnectError) as info:
        _rtc()
    assert "asimov-sdk[livekit]" in str(info.value)
    with pytest.raises(ConnectError):
        LiveKitTransport("ws://x", "r", token="t").open()


def test_a_fresh_interpreter_imports_the_sdk_with_livekit_blocked():
    """Import-time proof, in a process of its own: `import asimov_sdk` must not reach for
    livekit, nor may `Robot.connect` / the transport classes."""
    code = (
        "import sys\n"
        "class B:\n"
        "    def find_spec(self, n, p=None, t=None):\n"
        "        assert not n.startswith('livekit'), 'the SDK imported livekit'\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import asimov_sdk\n"
        "from asimov_sdk import HybridTransport, LiveKitTransport, Robot\n"
        "LiveKitTransport('ws://x', 'r', token='t')\n"
        "HybridTransport('h', livekit_url='ws://x', room='r', token='t')\n"
        "assert hasattr(Robot, 'connect_livekit') and hasattr(Robot, 'connect_hybrid')\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_the_sdk_holds_no_livekit_secret():
    """The owner's rule: the SDK RECEIVES a token, it never mints one — so no identifier
    named api_key/api_secret exists anywhere in it. Parsed, not grepped, so the prose that
    says so does not trip the test."""
    import ast
    from pathlib import Path

    banned = {"api_key", "api_secret", "apikey", "apisecret"}
    src = Path(__file__).resolve().parents[2] / "src" / "asimov_sdk"
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if "_vendor" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            name = (
                node.arg
                if isinstance(node, ast.arg)
                else node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else node.arg or ""
                if isinstance(node, ast.keyword)
                else ""
            )
            if name and name.lower() in banned:
                offenders.append(f"{path.name}:{node.lineno} {name}")
    assert offenders == [], f"the SDK must never hold the LiveKit API secret: {offenders}"


# ── the client seam itself ────────────────────────────────────────────────────


def test_a_token_may_be_a_callable_so_it_can_be_refreshed():
    minted = []

    def provider() -> str:
        minted.append(1)
        return f"token-{len(minted)}"

    client = _LiveKitClient("ws://x", "r", token=provider)
    assert client._resolve_token() == "token-1"
    assert client._resolve_token() == "token-2", "a provider is called per join, not cached"
    assert _LiveKitClient("ws://x", "r", token="static")._resolve_token() == "static"
    with pytest.raises(ConnectError) as info:
        _LiveKitClient("ws://x", "r", token="")._resolve_token()
    assert "never holds the LiveKit API secret" in str(info.value)


def test_the_identity_is_read_out_of_the_token_never_chosen_by_the_caller():
    """A participant's identity is a claim inside the JWT and the server ignores whatever a
    client says about it. An `identity=` argument would therefore be a lie, so there is
    none — the SDK reads the token's `sub` back instead."""
    import base64
    import inspect
    import json

    def jwt(payload: dict) -> str:
        def seg(obj: dict) -> str:
            raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
            return raw.rstrip("=")

        return f"{seg({'alg': 'HS256'})}.{seg(payload)}.signature-we-never-check"

    assert identity_from_token(jwt({"sub": "sdk", "video": {"room": "r"}})) == "sdk"
    assert identity_from_token(jwt({"iss": "devkey"})) is None, "no sub is None, not a guess"
    assert identity_from_token("not-a-jwt") is None
    assert identity_from_token("") is None
    assert identity_from_token(jwt({"sub": ""})) is None

    for ctor in (LiveKitTransport.__init__, HybridTransport.__init__, _LiveKitClient.__init__):
        assert "identity" not in inspect.signature(ctor).parameters, (
            f"{ctor.__qualname__} takes an identity the LiveKit server would ignore"
        )
    for ctor in (Robot.connect_livekit, Robot.connect_hybrid):
        assert "identity" not in inspect.signature(ctor).parameters


def test_the_transport_reports_the_identity_it_actually_joined_as(edge):
    """`endpoint` names it too: two SDK sessions in one room differ only by identity, and an
    error naming the room alone would not say which of them went quiet."""
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"sub": "operator-7"}).encode()).decode()
    token = f"aGVhZGVy.{payload.rstrip('=')}.sig"
    client = FakeLiveKitClient(edge, token=token)
    tx = LiveKitTransport("ws://sfu.local", "asimov-42", token=token, client=client)
    assert tx.identity is None and tx.endpoint == "asimov-42@ws://sfu.local"
    tx.open()
    try:
        assert tx.identity == "operator-7"
        assert tx.endpoint == "asimov-42@ws://sfu.local as operator-7"
    finally:
        tx.close()
    assert tx.identity is None, "a closed room has no identity to report"


def test_the_media_wait_resolves_as_soon_as_video_is_up(edge):
    """A robot with a camera and no microphone is a real configuration; it must not pay the
    whole media budget at connect."""
    client = FakeLiveKitClient(edge, tracks=("camera",))
    tx = LiveKitTransport("ws://fake", "r", token="t", client=client, media_timeout=30.0)
    started = time.monotonic()
    tx.open()
    try:
        assert time.monotonic() - started < 5.0, "a mic-less robot waited out the whole budget"
        assert "camera" in tx.capabilities and "microphone" not in tx.capabilities
    finally:
        tx.close()


def test_a_track_that_lands_after_the_connect_still_works(edge):
    """It misses the RobotInfo snapshot — that is what media_timeout is the knob for — but
    it attaches and delivers, which is the forgiving direction."""
    client, robot = make_livekit_robot(edge, tracks=("camera",))
    robot.open(timeout=3.0)
    try:
        assert not robot.has("microphone"), "the snapshot is honest about what had arrived"
        client._set_tracks(frozenset({"camera", "microphone"}))  # the robot brought its mic up
        assert "microphone" in robot._tx.capabilities
        threading.Timer(0.02, client.push_audio, args=(1,)).start()
        assert next(robot.microphone.chunks(timeout=1.0)).sequence == 1
    finally:
        robot.close()


def test_a_livekit_frame_becomes_an_rgb8_Frame():
    """The one conversion that matters: LiveKit hands out I420, and ``Frame.to_numpy()``
    deliberately refuses yuv420. Faked ``rtc`` objects, so no livekit is needed."""

    class Converted:
        data = b"\x01\x02\x03" * 8

    class LkFrame:
        width, height, type = 4, 2, "I420"

        def convert(self, buffer_type):
            assert buffer_type == "RGB24"
            return Converted()

    class Event:
        frame = LkFrame()
        timestamp_us = 1_500

    class Rtc:
        class VideoBufferType:
            RGB24 = "RGB24"

    frame = _LiveKitClient._to_frame(Rtc, Event, 7)
    assert frame is not None
    assert frame.encoding == "rgb8" and frame.stride_bytes == 12 and frame.sequence == 7
    assert frame.timestamp_ns == 1_500_000 and len(frame.data) == 24


def test_a_data_packet_is_routed_by_topic():
    client = _LiveKitClient("ws://x", "r", token="t")
    seen: list[bytes] = []
    client.on_data("state", seen.append)

    class Packet:
        data = b"payload"
        topic = "state"

    client._on_data_received(Packet())
    Packet.topic = "something-else"
    client._on_data_received(Packet())
    assert seen == [b"payload"], "a packet on another topic is not a state sample"


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
    from asimov_sdk._media import _rgb_bytes

    # Four distinguishable BGR pixels in a 2x2 frame.
    bgr = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    frame = Frame(width=2, height=2, encoding="bgr8", data=bgr)
    assert _rgb_bytes(frame) == bytes([3, 2, 1, 6, 5, 4, 9, 8, 7, 12, 11, 10])

    # rgb8 is handed back untouched.
    assert _rgb_bytes(Frame(width=2, height=2, encoding="rgb8", data=bgr)) == bgr
