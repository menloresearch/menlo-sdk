"""Camera / microphone / speaker plumbing against a fake transport, and the honest
`UnsupportedError` on a wire that carries none of it."""

from __future__ import annotations

import io
import threading
import time

import pytest

from menlo.asimov import AudioChunk, Frame, Robot, UnsupportedError, WaitTimeoutError
from menlo.asimov._command import Velocity
from menlo.asimov._state import Joint, Mode, State
from tests.conftest import connect_udp


def _state(seq: int = 1) -> State:
    return State(
        mode=Mode.STAND,
        joints=(Joint("a", 0.0, None, None, None), Joint("b", 0.0, None, None, None)),
        gravity=(0.0, 0.0, -1.0),
        gyro=None,
        quat=None,
        error_flags=0,
        alerts=(),
        battery=None,
        sequence=seq,
        fw_timestamp_us=0,
        protocol_version=1,
    )


class FakeMediaTransport:
    """A transport that carries everything, driven by the test."""

    kind = "udp"
    endpoint = "fake:0"
    default_outcome_timeout = 0.1
    capabilities = frozenset({"drive", "state", "camera", "microphone", "speaker"})

    def __init__(self) -> None:
        self.state_cbs: list = []
        self.frame_cbs: list = []
        self.audio_cbs: list = []
        self.played: list[AudioChunk] = []
        self.sent: list = []
        self._seq = 0

    def open(self) -> None:
        self.state_cbs[0](_state())  # first sample arrives at once

    def close(self) -> None: ...

    def send(self, command) -> int:
        self._seq += 1
        self.sent.append(command)
        return self._seq

    def subscribe_state(self, cb) -> None:
        self.state_cbs.append(cb)

    def subscribe_outcome(self, cb) -> None: ...

    def subscribe_controller_change(self, cb) -> None: ...

    def subscribe_frames(self, cb) -> None:
        self.frame_cbs.append(cb)

    def subscribe_audio(self, cb) -> None:
        self.audio_cbs.append(cb)

    def play_audio(self, chunk: AudioChunk) -> None:
        self.played.append(chunk)

    # test helpers
    def push_frame(self, n: int) -> Frame:
        f = Frame(width=4, height=2, encoding="rgb8", data=bytes(24), sequence=n)
        for cb in self.frame_cbs:
            cb(f)
        return f

    def push_audio(self, n: int) -> AudioChunk:
        a = AudioChunk(16_000, 1, 160, "pcm_s16le", bytes(320), sequence=n)
        for cb in self.audio_cbs:
            cb(a)
        return a


@pytest.fixture
def media():
    tx = FakeMediaTransport()
    robot = Robot(tx)
    robot.open(timeout=1.0, allow_version_skew=True)
    yield tx, robot
    robot.close()


def test_capabilities_come_from_the_transport(media):
    tx, robot = media
    assert robot.has("camera") and robot.has("speaker") and not robot.has("battery")
    assert robot.info.capabilities == tx.capabilities


def test_camera_latest_and_subscribe(media):
    tx, robot = media
    assert robot.camera.latest() is None
    seen = []
    robot.camera.subscribe(seen.append)
    f = tx.push_frame(1)
    assert robot.camera.latest() is f and seen == [f]
    assert f.shape == (2, 4)


def test_camera_frames_yields_as_they_arrive_and_times_out_when_quiet(media):
    tx, robot = media
    it = robot.camera.frames(timeout=0.3)

    def feed() -> None:
        for n in range(3):
            time.sleep(0.02)
            tx.push_frame(n)

    threading.Thread(target=feed).start()
    got = [next(it).sequence for _ in range(3)]
    assert got == [0, 1, 2]
    with pytest.raises(WaitTimeoutError):
        next(it)  # nobody is pushing any more


def test_microphone_chunks_and_speaker_play(media):
    tx, robot = media
    it = robot.microphone.chunks(timeout=0.5)
    threading.Timer(0.02, tx.push_audio, args=(7,)).start()
    chunk = next(it)
    assert chunk.sequence == 7 and chunk.duration_s == pytest.approx(0.01)
    robot.speaker.play_pcm(bytes(3200), sample_rate_hz=16_000)
    assert len(tx.played) == 1 and tx.played[0].samples_per_channel == 1600


def test_the_udp_lane_says_unsupported_not_silence(edge, robot):
    assert robot.info.capabilities == frozenset({"drive", "state"})
    assert not robot.has("camera")
    with pytest.raises(UnsupportedError) as info:
        robot.camera.latest()
    assert info.value.capability == "camera" and "udp" in str(info.value)
    with pytest.raises(UnsupportedError):
        robot.microphone.latest()
    with pytest.raises(UnsupportedError):
        robot.speaker.play_pcm(b"\x00\x00")
    assert isinstance(info.value, Exception) and Velocity().is_zero  # sanity, imports used


def test_battery_is_a_capability_when_the_robot_reports_one(edge):
    edge.state.battery.voltage_v = 48.2
    edge.state.battery.soc_percent = 77.0
    time.sleep(0.05)
    with connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=2,
    ) as r:
        assert r.has("battery")
        assert r.state.battery is not None and r.state.battery.soc_percent == pytest.approx(77.0)
        assert r.state.battery.protecting is False


def test_first_media_attachment_happens_once_under_concurrency(media):
    tx, robot = media
    start = threading.Barrier(8)

    def first_call() -> None:
        start.wait()
        robot.camera.latest()

    threads = [threading.Thread(target=first_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(tx.frame_cbs) == 1, "two first callers attached twice; frames would be duplicated"


def test_has_tracks_a_media_capability_disappearing_mid_session(media):
    """`has()` must answer for NOW, not for what was true at connect.

    `RobotInfo` is frozen at connect, but a room lane loses its camera when the track
    unsubscribes. A script gating on `has("camera")` — the documented safe pattern —
    should then skip cleanly instead of taking the UnsupportedError it was avoiding.
    """
    tx, robot = media
    assert robot.has("camera")
    assert "camera" in robot.info.capabilities  # the connect-time snapshot keeps it

    tx.capabilities = frozenset(c for c in tx.capabilities if c != "camera")

    assert not robot.has("camera"), "has() must follow the transport, not the snapshot"
    with pytest.raises(UnsupportedError):
        robot.require("camera")


# ── Frame.to_jpeg ─────────────────────────────────────────────────────────────


def _pil():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow is not installed here; to_jpeg() raises ImportError naming it")
    return Image


def test_to_jpeg_encodes_rgb_bgr_and_gray_frames_and_passes_jpeg_through():
    Image = _pil()
    # Solid 8x8 blocks: JPEG averages chroma over 2x2 pixels, so a single pixel says nothing.
    red_rgb = bytes([255, 0, 0]) * 64
    red_bgr = bytes([0, 0, 255]) * 64
    for encoding, data in (("rgb8", red_rgb), ("bgr8", red_bgr)):
        out = Frame(width=8, height=8, encoding=encoding, data=data).to_jpeg(quality=95)
        assert out[:3] == b"\xff\xd8\xff", f"{encoding}: not a JPEG"
        img = Image.open(io.BytesIO(out))
        assert img.size == (8, 8) and img.mode == "RGB"
        r, g, b = img.getpixel((4, 4))
        assert r > 200 and g < 60 and b < 60, f"{encoding}: expected red, got {(r, g, b)}"
    # a padded stride is honoured, and gray stays gray
    row = bytes([255] * 8 + [9, 9, 9, 9])
    padded = Frame(width=8, height=8, encoding="gray8", data=row * 8, stride_bytes=12)
    img = Image.open(io.BytesIO(padded.to_jpeg()))
    assert img.mode == "L" and img.size == (8, 8) and img.getpixel((7, 7)) > 240
    already = Frame(width=1, height=1, encoding="jpeg", data=b"\xff\xd8\xff\xd9")
    assert already.to_jpeg() is already.data, "a JPEG frame is not re-encoded"


def test_to_jpeg_refuses_what_is_not_pixels_and_a_bad_quality():
    frame = Frame(width=2, height=2, encoding="h264", data=bytes(8))
    with pytest.raises(ValueError, match="not raw RGB"):
        frame.to_jpeg()
    with pytest.raises(ValueError, match="quality"):
        Frame(width=1, height=1, encoding="rgb8", data=bytes(3)).to_jpeg(quality=0)


def test_to_jpeg_quality_is_a_real_knob():
    _pil()
    import random

    rnd = random.Random(1)
    noisy = bytes(rnd.randrange(256) for _ in range(64 * 64 * 3))
    frame = Frame(width=64, height=64, encoding="rgb8", data=noisy)
    assert len(frame.to_jpeg(quality=20)) < len(frame.to_jpeg(quality=95))
