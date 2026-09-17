"""Shared fixtures: a fake edge that speaks the real wire, and a live-rig gate."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable

import pytest

from asimov_sdk import Applied, LinkLostError, Refused, Robot
from asimov_sdk.transport._livekit_client import identity_from_token
from asimov_sdk.transport.livekit import HybridTransport, LiveKitTransport
from asimov_sdk.transport.udp import UdpTransport


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

    def __init__(self, *, state_hz: float = 100.0, alerts_every: int = 1) -> None:
        from asimov_sdk._proto import load

        pb = load()  # same bindings the SDK uses, whichever source it resolved to
        self._cmd_pb, self._common_pb, self._st_pb = pb.command, pb.common, pb.state
        self.command_port = _free_port()
        self.state_port = _free_port()
        self.received: list = []  # decoded RobotCommand protos, in arrival order
        self.state = self._st_pb.RobotState(
            current_mode=self._common_pb.CONTROL_MODE_DAMP, protocol_version=1
        )
        self.state.joint_pos.extend([0.0] * 25)
        self.state.projected_gravity.extend([0.0, 0.0, -1.0])
        self.pushing = True
        self.alerts_every = alerts_every  # firmware ships the alert block every 20th frame
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
                if self.alerts_every > 1 and seq % self.alerts_every:
                    frame = self._st_pb.RobotState()
                    frame.CopyFrom(self.state)
                    del frame.active_alerts[:]
                    out.sendto(frame.SerializeToString(), ("127.0.0.1", self.state_port))
                else:
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


def connect_udp(
    host: str,
    *,
    command_port: int = 8850,
    state_bind: tuple[str, int] = ("0.0.0.0", 8851),
    state_source: str | None = None,
    limits=None,
    link_timeout: float | None = None,
    **connect_kw,
) -> Robot:
    """``Robot(ConnectionConfig(udp=...)).connect("udp", ...)`` in one call, for tests that
    only care about the UDP lane."""
    from asimov_sdk.connection import ConnectionConfig, UdpConfig

    cfg = ConnectionConfig(
        udp=UdpConfig(
            host, command_port=command_port, state_bind=state_bind, state_source=state_source
        )
    )
    robot_kw = {}
    if limits is not None:
        robot_kw["limits"] = limits
    if link_timeout is not None:
        robot_kw["link_timeout"] = link_timeout
    return Robot(cfg, **robot_kw).connect("udp", **connect_kw)


@pytest.fixture
def edge():
    e = FakeEdge()
    yield e
    e.close()


class SeamUdpTransport(UdpTransport):
    """The real transport plus one test-only door: deliver a verdict as if the edge sent it."""

    def deliver_outcome(self, outcome: Applied | Refused) -> None:
        for cb in tuple(self._on_outcome):
            cb(outcome)


@pytest.fixture
def robot(edge):
    tx = SeamUdpTransport(
        "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
    )
    r = Robot(tx)
    r.open(timeout=3.0)
    yield r
    r.close()


class FakeLiveKitClient:
    """The ``LiveKitClient`` seam with the room replaced by loopback UDP to the FakeEdge.

    This is why no unit test needs livekit installed, or a server running: the SDK's only
    ``livekit`` imports live behind this seam, so faking the seam exercises everything
    above it — the topics, the wire bytes, the capability honesty, the media plumbing.

    ``carry_state=False`` is the hybrid lane's shape: media only, because control and state
    are on the UDP transport beside it.
    """

    def __init__(
        self,
        edge: FakeEdge,
        *,
        tracks: tuple[str, ...] = ("camera", "microphone"),
        carry_state: bool = True,
        token: str = "fake-token",
    ) -> None:
        self._edge = edge
        self._room_tracks = frozenset(tracks)
        self._carry_state = carry_state
        self._token = token
        self.tracks: frozenset[str] = frozenset()
        self.identity: str | None = None  # read out of the token at connect, as the real one does
        self.connected = False
        self.played: list = []  # AudioChunks handed to the speaker track
        self.published: list[tuple[str, bytes]] = []  # (topic, payload)
        self._data_track_cbs: dict[str, list] = {}
        self._video_cbs: list = []
        self._audio_cbs: list = []
        self._track_cbs: list = []
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()

    # ── the seam ─────────────────────────────────────────────────────────────
    def connect(self) -> None:
        if self.connected:
            raise RuntimeError("already connected")
        if self._carry_state:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", self._edge.state_port))
            sock.settimeout(0.1)
            self._sock = sock
            self._stop.clear()
            self._reader = threading.Thread(target=self._read, daemon=True)
            self._reader.start()
        self.identity = identity_from_token(self._token)
        self.connected = True
        self._set_tracks(self._room_tracks)

    def close(self) -> None:
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self.connected = False
        self.identity = None
        self._set_tracks(frozenset())

    def wait_for_tracks(self, timeout: float) -> frozenset[str]:
        return self.tracks

    def publish_data(self, payload: bytes, *, topic: str) -> None:
        if not self.connected:
            raise LinkLostError("the fake room is not joined")
        self.published.append((topic, payload))
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out.sendto(payload, ("127.0.0.1", self._edge.command_port))
        out.close()

    def publish_audio(self, chunk) -> None:
        if not self.connected:
            raise LinkLostError("the fake room is not joined")
        self.played.append(chunk)

    def on_data_track(self, name: str, callback) -> None:
        self._data_track_cbs.setdefault(name, []).append(callback)

    def on_video(self, callback) -> None:
        self._video_cbs.append(callback)

    def on_audio(self, callback) -> None:
        self._audio_cbs.append(callback)

    def on_tracks(self, callback) -> None:
        self._track_cbs.append(callback)

    # ── plumbing + test helpers ──────────────────────────────────────────────
    def _read(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                data, _ = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            # The FakeEdge speaks UDP, which carries no edge clock: user_timestamp=None.
            for cb in tuple(self._data_track_cbs.get("state", ())):
                cb(data, None)

    def _set_tracks(self, tracks: frozenset[str]) -> None:
        if tracks == self.tracks:
            return
        self.tracks = tracks
        for cb in tuple(self._track_cbs):
            cb(tracks)

    def push_frame(self, n: int):
        from asimov_sdk import Frame

        f = Frame(width=4, height=2, encoding="rgb8", data=bytes(24), stride_bytes=12, sequence=n)
        for cb in tuple(self._video_cbs):
            cb(f)
        return f

    def push_audio(self, n: int, *, samples: int = 160):
        from asimov_sdk import AudioChunk

        a = AudioChunk(16_000, 1, samples, "pcm_s16le", bytes(2 * samples), sequence=n)
        for cb in tuple(self._audio_cbs):
            cb(a)
        return a


def make_livekit_robot(edge: FakeEdge, **kw) -> tuple[FakeLiveKitClient, Robot]:
    """Mode B: everything over the (faked) room."""
    client = FakeLiveKitClient(edge, **kw)
    tx = LiveKitTransport("ws://fake", "asimov-room", token="test-token", client=client)
    return client, Robot(tx)


def make_hybrid_robot(edge: FakeEdge, **kw) -> tuple[FakeLiveKitClient, Robot]:
    """Mode A: UDP control, (faked) room media."""
    client = FakeLiveKitClient(edge, carry_state=False, **kw)
    tx = HybridTransport(
        "127.0.0.1",
        livekit_url="ws://fake",
        room="asimov-room",
        token="test-token",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        client=client,
    )
    return client, Robot(tx)


@pytest.fixture
def livekit_robot(edge):
    client, r = make_livekit_robot(edge)
    r.open(timeout=3.0)
    yield client, r
    r.close()


@pytest.fixture
def hybrid_robot(edge):
    client, r = make_hybrid_robot(edge)
    r.open(timeout=3.0)
    yield client, r
    r.close()


@pytest.fixture(params=["udp", "hybrid", "livekit"])
def any_robot(request, edge):
    """The same robot over each of the three lanes. A behaviour that is not mode-agnostic
    is not done: every promise below is asserted three times."""
    if request.param == "udp":
        tx = SeamUdpTransport(
            "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
        )
        r: Robot = Robot(tx)
    else:
        maker = make_hybrid_robot if request.param == "hybrid" else make_livekit_robot
        _client, r = maker(edge)
    r.open(timeout=3.0)
    yield request.param, r
    r.close()


@pytest.fixture
def live_host() -> str:
    """A real robot or studio rig, named by ASIMOV_SDK_LIVE_HOST. Skips LOUDLY otherwise."""
    host = os.environ.get("ASIMOV_SDK_LIVE_HOST")
    if not host:
        pytest.skip("ASIMOV_SDK_LIVE_HOST not set — no live robot to drive")
    return host
