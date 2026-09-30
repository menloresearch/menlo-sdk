"""Shared fixtures: a fake edge that speaks the real wire, and a live-rig gate."""

from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from menlo.asimov import Applied, LinkLostError, Refused, Robot
from menlo.asimov.transport._livekit_client import identity_from_token
from menlo.asimov.transport.livekit import HybridTransport, LiveKitTransport
from menlo.asimov.transport.udp import UdpTransport

#: ``CONTROL_MODE_FAULT_DAMP``, by value: asimov.io protocol v1.3.0 adds it, and the SDK
#: reads robot modes by value, so it works whatever version of the bindings is installed.
FAULT_DAMP = 5


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def ankle_coupling(positions: list[float]) -> list[float]:
    """What the biped firmware does to a trajectory's ankle entries: it reads them as the
    ankle's (pitch, roll), limits them to 0.35 and 0.1 rad, and drives motors A and B."""
    if len(positions) != 25:
        return positions
    for a, b in ((4, 5), (10, 11)):
        pitch = max(-0.35, min(0.35, positions[a]))
        roll = max(-0.1, min(0.1, positions[b]))
        positions[a] = 2.02 * pitch - 0.8 * roll
        positions[b] = -2.02 * pitch - 0.8 * roll
    return positions


class FakeEdge:
    """The edge's UDP lane, as the SDK sees it: bare ``RobotCommand`` in, bare
    ``RobotState`` pushed out to a configured host at a configured rate.

    Deliberately shaped like ``asimov-edge``'s ``UdpConnector`` (sender address ignored,
    state to ONE destination), so a test that passes here says something about the robot.
    Records every decoded command; the state it pushes is whatever the test sets.

    ``firmware=True`` makes the robot mode follow the commands the way the Motion Control
    Board firmware does: STAND and DAMP take effect at once; MOVE (a velocity or a
    trajectory) is entered only from STAND once the robot has been upright (gravity z below
    -0.87) for ``arm_hold_s``, and until then the robot stays in STAND; MOVE -> STAND stays
    armed; a velocity or trajectory while DAMPed is dropped (the edge's DAMP gate); a
    trajectory in MOVE sets the reported joint positions to its targets (after the
    firmware's ankle coupling); and a latched
    fault (``fault()``, or ``fault_damp()`` for robot mode FAULT_DAMP) holds the robot limp
    and ignores STAND and MOVE.
    """

    def __init__(
        self,
        *,
        state_hz: float = 100.0,
        alerts_every: int = 1,
        firmware: bool = False,
        arm_hold_s: float = 0.5,
    ) -> None:
        from menlo.asimov._proto import load

        pb = load()  # same bindings the SDK uses, whichever source it resolved to
        self._cmd_pb, self._common_pb, self._st_pb = pb.command, pb.common, pb.state
        self.command_port = _free_port()
        self.state_port = _free_port()
        self.received: list = []  # decoded RobotCommand protos, in arrival order
        self.arrived: list[float] = []  # monotonic receipt time of each entry in received
        self.state = self._st_pb.RobotState(
            current_mode=self._common_pb.CONTROL_MODE_DAMP, protocol_version=1
        )
        self.state.joint_pos.extend([0.0] * 25)
        self.state.projected_gravity.extend([0.0, 0.0, -1.0])
        self.pushing = True
        self.alerts_every = alerts_every  # firmware ships the alert block every 20th frame
        self.firmware = firmware
        self.arm_hold_s = arm_hold_s
        self.armed = False
        self.armed_at: float | None = None  # monotonic time the fake firmware armed
        self.move_entered_at: float | None = None
        self.first_velocity_at: float | None = None  # monotonic receipt of the first velocity
        self._upright_since: float | None = None
        self._fw_lock = threading.Lock()
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
            if c.HasField("policy") and self.first_velocity_at is None:
                self.first_velocity_at = time.monotonic()
            self.arrived.append(time.monotonic())
            self.received.append(c)
            if self.firmware:
                self._follow(c)

    # ── the firmware's mode machine (firmware=True) ──────────────────────────
    def _mode(self) -> int:
        return int(self.state.current_mode)

    def _enter(self, mode: int) -> None:
        stand, move = self._common_pb.CONTROL_MODE_STAND, self._common_pb.CONTROL_MODE_MOVE
        old = self._mode()
        if old == mode:
            return
        if not (old == move and mode == stand):
            self.armed = False
            self._upright_since = None
        if mode == move:
            self.move_entered_at = time.monotonic()
        self.state.current_mode = mode

    def _follow(self, c) -> None:
        pb = self._common_pb
        with self._fw_lock:
            drive = c.HasField("policy") or c.HasField("all_trajectory")
            if self.state.error_flags or self._mode() == FAULT_DAMP:
                return  # latched: limp until the firmware restarts
            if drive:
                mode = self._mode()
                if mode == pb.CONTROL_MODE_DAMP:
                    return  # the edge drops a velocity or trajectory while DAMPed
                if mode == pb.CONTROL_MODE_STAND and self.armed:
                    self._enter(pb.CONTROL_MODE_MOVE)
                if c.HasField("all_trajectory") and self._mode() == pb.CONTROL_MODE_MOVE:
                    # The joints reach a trajectory's targets at once: tracking is perfect.
                    del self.state.joint_pos[:]
                    self.state.joint_pos.extend(ankle_coupling(list(c.all_trajectory.positions)))
            elif c.mode == pb.CONTROL_MODE_STAND:
                self._enter(pb.CONTROL_MODE_STAND)
            elif c.mode == pb.CONTROL_MODE_DAMP:
                self._enter(pb.CONTROL_MODE_DAMP)

    def _arm_tick(self) -> None:
        with self._fw_lock:
            g = list(self.state.projected_gravity)
            now = time.monotonic()
            if g and g[2] < -0.87:
                if self._upright_since is None:
                    self._upright_since = now
                elif (
                    not self.armed
                    and self._mode() == self._common_pb.CONTROL_MODE_STAND
                    and now - self._upright_since >= self.arm_hold_s
                ):
                    self.armed = True
                    self.armed_at = now
            else:
                self._upright_since = None

    def fault(self, alert_id: int = 7) -> None:
        """Latch a critical alert (default FALL_DETECTED): DAMP, error_flags set."""
        with self._fw_lock:
            self.state.error_flags |= 1 | (1 << (1 + alert_id))
            self._enter(self._common_pb.CONTROL_MODE_DAMP)

    def fault_damp(self) -> None:
        """Robot mode FAULT_DAMP: the firmware's latched emergency damping."""
        with self._fw_lock:
            self._enter(FAULT_DAMP)

    def _push(self) -> None:
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        period = 1.0 / self._hz
        seq = 0
        while not self._stop.is_set():
            if self.firmware:
                self._arm_tick()
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

    def velocities_between(
        self, start: float, end: float = math.inf
    ) -> list[tuple[float, float, float]]:
        """The velocities received from monotonic time ``start`` until ``end``."""
        return [
            (round(c.policy.vx, 4), round(c.policy.vy, 4), round(c.policy.vyaw, 4))
            for c, at in zip(tuple(self.received), tuple(self.arrived), strict=False)
            if start <= at < end and c.HasField("policy")
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
    from menlo.asimov.connection import ConnectionConfig, UdpConfig

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


def put_in(robot: Robot, edge: FakeEdge, mode: str) -> None:
    """Make the fake robot report robot mode ``mode`` and wait until ``robot`` has seen it:
    a motion command checks the robot mode before it sends."""
    edge.set_mode(mode)
    robot.wait_until(lambda s: s.mode.name == mode.upper(), timeout=2.0)


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
    above it: the topics, the wire bytes, the capability honesty, the media plumbing.

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
        from menlo.asimov import Frame

        f = Frame(width=4, height=2, encoding="rgb8", data=bytes(24), stride_bytes=12, sequence=n)
        for cb in tuple(self._video_cbs):
            cb(f)
        return f

    def push_audio(self, n: int, *, samples: int = 160):
        from menlo.asimov import AudioChunk

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


class FakeManager(HTTPServer):
    """A stand-in for asimov-manager's POST /api/livekit/token."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict, dict]] = []  # (headers, body)
        self.status = 200
        self.reply: dict = {
            "url": "ws://robot:7880",
            "room": "robot-menlo-0042",
            "token": "jwt-1",
            "identity": "sdk-abc",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        self.minted = 0
        self.raw_body: bytes | None = None  # when set, answered verbatim with raw_type
        self.raw_type = "text/html"
        self.redirect_to: str | None = None  # when set, every request is a 302 there
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw.startswith(b"{") else {}
                server.requests.append((dict(self.headers), body))
                if server.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", server.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if server.raw_body is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", server.raw_type)
                    self.send_header("Content-Length", str(len(server.raw_body)))
                    self.end_headers()
                    self.wfile.write(server.raw_body)
                    return
                server.minted += 1
                reply = dict(server.reply)
                if server.status == 200:
                    reply["token"] = f"jwt-{server.minted}"
                payload = json.dumps(
                    reply if server.status == 200 else {"error": "no LiveKit here"}
                )
                self.send_response(server.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *_: object) -> None:
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server_address[1]}"
        threading.Thread(target=self.serve_forever, daemon=True).start()


@pytest.fixture
def manager():
    server = FakeManager()
    yield server
    server.shutdown()


def route_manager_rooms_to(edge: FakeEdge, monkeypatch) -> list[str]:
    """Make every ``LiveKitTransport`` a ``ManagerConfig`` builds join the FakeEdge through
    the fake client instead of a real room. Returns the list the tokens each join presented
    are appended to, so a test can see which mint a join used."""
    from menlo.asimov import connection

    seen_tokens: list[str] = []
    real = connection.LiveKitTransport

    class Capturing(real):  # type: ignore[misc,valid-type]
        def __init__(self, url, room, *, token, **kw):
            client = FakeLiveKitClient(edge, token=token() if callable(token) else token)
            seen_tokens.append(client._token)
            super().__init__(url, room, token=token, client=client, **kw)

    monkeypatch.setattr(connection, "LiveKitTransport", Capturing)
    return seen_tokens


@pytest.fixture(autouse=True)
def _own_environment(monkeypatch, tmp_path):
    """No test reads the developer's ~/.menlo or environment, and none writes there."""
    monkeypatch.setenv("MENLO_HOME", str(tmp_path / "asimov-home"))
    for name in tuple(os.environ):
        if name.startswith("MENLO_") and name not in ("MENLO_HOME",) and "SDK_" not in name:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def live_host() -> str:
    """A real robot, named by MENLO_SDK_LIVE_HOST. Skips LOUDLY otherwise."""
    host = os.environ.get("MENLO_SDK_LIVE_HOST")
    if not host:
        pytest.skip("MENLO_SDK_LIVE_HOST not set: no live robot to drive")
    return host
