"""ConnectionConfig → Robot(cfg) → robot.connect(mode): the lane is chosen last, each lane's
fields live on their own class, and nothing touches the network before connect()."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from asimov_sdk import (
    ConnectError,
    ConnectionConfig,
    LinkLostError,
    LiveKitConfig,
    ManagerConfig,
    NotConnectedError,
    Robot,
    UdpConfig,
)
from asimov_sdk.connection import MODES
from asimov_sdk.transport import HybridTransport, LiveKitTransport, UdpTransport
from tests.conftest import FakeLiveKitClient

# ── the config itself ─────────────────────────────────────────────────────────


def test_available_modes_follow_the_slots_that_are_set():
    udp = UdpConfig("10.0.0.5")
    lk = LiveKitConfig(url="ws://x:7880", room="robot-x", token="t")
    assert ConnectionConfig().available_modes() == ()
    assert ConnectionConfig(udp=udp).available_modes() == ("udp",)
    assert ConnectionConfig(livekit=lk).available_modes() == ("livekit",)
    assert ConnectionConfig(udp=udp, livekit=lk).available_modes() == MODES


def test_transport_for_builds_the_lane_without_opening_anything():
    cfg = ConnectionConfig(
        udp=UdpConfig("10.0.0.5", command_port=18850, state_bind=("127.0.0.1", 18851)),
        livekit=LiveKitConfig(url="ws://x:7880", room="robot-x", token="t"),
    )
    udp = cfg.transport_for("udp")
    assert isinstance(udp, UdpTransport) and udp.endpoint == "10.0.0.5:18850"
    hybrid = cfg.transport_for("hybrid")
    assert isinstance(hybrid, HybridTransport) and "robot-x@ws://x:7880" in hybrid.endpoint
    livekit = cfg.transport_for("livekit")
    assert isinstance(livekit, LiveKitTransport) and livekit.endpoint.startswith("robot-x@")


def test_a_missing_slot_is_a_connect_error_that_names_it():
    with pytest.raises(ConnectError, match=r"needs the livekit slot.*can connect on: udp"):
        ConnectionConfig(udp=UdpConfig("h")).transport_for("livekit")
    with pytest.raises(ConnectError, match="needs the udp slot"):
        ConnectionConfig(livekit=LiveKitConfig("ws://x", "r", "t")).transport_for("hybrid")
    with pytest.raises(ConnectError, match="can connect on: nothing"):
        ConnectionConfig().transport_for("udp")
    with pytest.raises(ValueError, match="unknown mode"):
        ConnectionConfig(udp=UdpConfig("h")).transport_for("teleport")  # type: ignore[arg-type]


def test_each_lane_config_has_only_its_own_fields():
    """The point of separate classes: an editor never offers a token on the UDP lane."""
    assert set(UdpConfig.__dataclass_fields__) == {
        "host",
        "command_port",
        "state_bind",
        "state_source",
    }
    assert set(LiveKitConfig.__dataclass_fields__) == {"url", "room", "token"}
    assert set(ManagerConfig.__dataclass_fields__) == {"url", "credential", "label", "timeout"}


# ── ManagerConfig: the SDK mints through the manager, the user never sees a token ────


class _Manager(HTTPServer):
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
    server = _Manager()
    yield server
    server.shutdown()


def test_manager_config_asks_the_manager_with_the_credential_and_mints_per_join(manager):
    cfg = ManagerConfig(url=manager.url, credential="cred-123", label="laptop")
    lk = cfg.resolve()
    assert lk.url == "ws://robot:7880" and lk.room == "robot-menlo-0042"
    headers, body = manager.requests[0]
    assert headers["Authorization"] == "Bearer cred-123"
    assert body == {"label": "laptop"}
    assert manager.requests[0][0]["Content-Type"] == "application/json"
    # the token is a callable: the first join uses the token already minted (nothing minted
    # is thrown away), every later join asks the manager again
    assert callable(lk.token)
    assert lk.token() == "jwt-1"
    assert lk.token() == "jwt-2"
    assert manager.minted == 2


def test_manager_config_builds_the_livekit_transport_from_the_managers_answer(manager):
    cfg = ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential="c"))
    tx = cfg.transport_for("livekit")
    assert isinstance(tx, LiveKitTransport)
    assert tx.endpoint.startswith("robot-menlo-0042@ws://robot:7880")
    assert manager.minted == 1  # one round trip; its token is the one the first join uses


def test_a_manager_that_refuses_is_a_connect_error_carrying_its_reason(manager):
    manager.status = 503
    with pytest.raises(ConnectError, match=r"HTTP 503.*no LiveKit here"):
        ManagerConfig(url=manager.url, credential="c").resolve()


def test_an_unreachable_manager_is_a_connect_error_naming_the_url():
    with pytest.raises(ConnectError, match=r"could not reach the manager at http://127\.0\.0\.1:9"):
        ManagerConfig(url="http://127.0.0.1:9", credential="c", timeout=0.5).resolve()


def test_an_old_manager_without_the_token_fields_is_reported(manager):
    manager.reply = {"status": "ok"}
    with pytest.raises(ConnectError, match="answered without url, room"):
        ManagerConfig(url=manager.url, credential="c").resolve()


def test_a_redirecting_manager_is_refused_and_the_credential_stays_home(manager):
    """urllib follows redirects WITH the Authorization header; the SDK must not."""
    elsewhere = _Manager()
    try:
        manager.redirect_to = elsewhere.url + "/api/livekit/token"
        with pytest.raises(ConnectError, match=r"redirected to .*use that address"):
            ManagerConfig(url=manager.url, credential="bearer-secret").resolve()
        assert elsewhere.requests == []  # the credential never left for the other host
        assert manager.requests[0][0]["Authorization"] == "Bearer bearer-secret"
    finally:
        elsewhere.shutdown()


@pytest.mark.parametrize(
    ("body", "ctype", "match"),
    [
        (b"<html>login</html>", "text/html", "did not answer with JSON"),
        (b"[1, 2, 3]", "application/json", "JSON list, not an object"),
        (b'"nope"', "application/json", "JSON str, not an object"),
        (b"\xff\xfe", "application/octet-stream", "did not answer with JSON"),
        (b"x" * (70 * 1024), "application/json", "more than"),
    ],
)
def test_a_non_token_answer_is_a_connect_error_not_a_traceback(manager, body, ctype, match):
    manager.raw_body, manager.raw_type = body, ctype
    with pytest.raises(ConnectError, match=match):
        ManagerConfig(url=manager.url, credential="c").resolve()


def test_secrets_stay_out_of_repr():
    cfg = ConnectionConfig(
        udp=UdpConfig("h"),
        livekit=ManagerConfig(url="http://m", credential="sdk_live_SECRET"),
    )
    assert "SECRET" not in repr(cfg)
    assert "tok-SECRET" not in repr(LiveKitConfig("ws://x", "r", "tok-SECRET"))
    with pytest.raises(ValueError, match="timeout must be positive"):
        ManagerConfig(url="http://m", credential="c", timeout=0)


# ── Robot(cfg).connect(mode): bind, then choose, then switch ─────────────────────────


class _FakeLanes(ConnectionConfig):
    """Real UDP to the FakeEdge; the LiveKit lane through the unit suite's fake room."""

    def __init__(self, edge) -> None:
        object.__setattr__(
            self,
            "udp",
            UdpConfig(
                "127.0.0.1",
                command_port=edge.command_port,
                state_bind=("127.0.0.1", edge.state_port),
            ),
        )
        object.__setattr__(
            self, "livekit", LiveKitConfig(url="ws://fake", room="asimov-room", token="t")
        )
        object.__setattr__(self, "_edge", edge)
        object.__setattr__(self, "clients", [])

    def transport_for(self, mode, *, media_timeout=3.0, connect_timeout=10.0):
        if mode == "udp":
            return super().transport_for("udp")
        client = FakeLiveKitClient(self._edge, carry_state=(mode == "livekit"))
        self.clients.append(client)
        if mode == "livekit":
            return LiveKitTransport("ws://fake", "asimov-room", token="t", client=client)
        return HybridTransport(
            "127.0.0.1",
            livekit_url="ws://fake",
            room="asimov-room",
            token="t",
            command_port=self._edge.command_port,
            state_bind=("127.0.0.1", self._edge.state_port),
            client=client,
        )


def test_a_bound_robot_touches_nothing_until_connect(edge):
    robot = Robot(_FakeLanes(edge))
    assert not robot.connected
    with pytest.raises(NotConnectedError):
        _ = robot.state
    with pytest.raises(NotConnectedError, match=r"call connect\(mode\)"):
        robot.camera.latest()  # media resolves the transport lazily: none yet
    with pytest.raises(NotConnectedError, match=r"call connect\(mode\) instead of open"):
        robot.open()
    assert edge.received == []


def test_the_same_robot_switches_lanes_and_keeps_its_identity(edge):
    cfg = _FakeLanes(edge)
    robot = Robot(cfg)
    limits_before = robot.limits

    with robot.connect("udp", timeout=3.0) as r:
        assert r is robot
        assert robot.info.transport == "udp"
        assert not robot.has("camera")
        robot.stand()
    assert not robot.connected

    robot.connect("livekit", timeout=3.0)
    try:
        assert robot.info.transport == "livekit"
        assert robot.has("camera")
        assert robot.camera.latest() is None  # first use attaches to the NEW transport
        frame = cfg.clients[-1].push_frame(1)
        assert robot.camera.latest() is frame  # the camera followed the new transport
        assert robot.limits is limits_before  # the Robot is the same object with the same knobs
    finally:
        robot.close()

    robot.connect("hybrid", timeout=3.0)
    try:
        assert robot.info.transport == "hybrid"
        assert robot.has("camera")
        assert robot.camera.latest() is None  # nothing from the previous lane leaks through
        cfg.clients[-1].push_frame(2)
        assert robot.camera.latest() is not None
    finally:
        robot.close()


def test_a_lane_this_robot_left_cannot_write_into_the_next_session(edge):
    """A closed transport keeps its subscriber list and its reader may outlive close();
    nothing it delivers may become the new session's frame, audio or state."""
    cfg = _FakeLanes(edge)
    robot = Robot(cfg).connect("livekit", timeout=3.0)
    try:
        assert robot.camera.latest() is None
        assert robot.microphone.latest() is None  # both streams attached to this lane
        old = cfg.clients[-1]
        old.push_frame(1)
        old.push_audio(1)
        old.push_audio(2)
        assert robot.camera.latest() is not None
        assert next(iter(robot.microphone.chunks(timeout=1.0))).sequence == 1
    finally:
        robot.close()

    robot.connect("hybrid", timeout=3.0)
    try:
        assert robot.camera.latest() is None  # attaches to the new lane
        assert robot.microphone.latest() is None
        old.push_frame(1234)  # the previous room speaks after we left it
        old.push_audio(3)
        assert robot.camera.latest() is None
        with pytest.raises(TimeoutError):
            next(iter(robot.microphone.chunks(timeout=0.2)))  # chunk 2 did not replay either
        assert robot.microphone.dropped == 0
        cfg.clients[-1].push_frame(7)
        cfg.clients[-1].push_audio(9)
        assert robot.camera.latest().sequence == 7
        assert next(iter(robot.microphone.chunks(timeout=1.0))).sequence == 9
    finally:
        robot.close()


def _keepalive_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "asimov-sdk-keepalive"]


def test_reconnecting_from_on_link_lost_leaves_exactly_one_keepalive(edge):
    """close()+connect() inside on_link_lost runs ON the keepalive thread. The old thread
    must exit; a second one re-sending into the edge would double the command rate."""
    before = len(_keepalive_threads())
    robot = Robot(_FakeLanes(edge))
    reconnected = threading.Event()

    def reconnect(exc: LinkLostError) -> None:
        robot.link_timeout = 2.0  # back to normal before the new session starts
        robot.close()
        robot.connect("udp", timeout=3.0)
        reconnected.set()

    robot.on_link_lost = reconnect
    robot.connect("udp", timeout=3.0)
    try:
        robot.link_timeout = 0.0  # the next keepalive tick declares the link lost
        assert reconnected.wait(3.0)
        time.sleep(0.3)  # long enough for a stale thread to take its next tick
        assert len(_keepalive_threads()) == before + 1
        assert robot.connected
    finally:
        robot.close()
    time.sleep(0.3)
    assert len(_keepalive_threads()) == before


def test_a_frames_generator_held_across_a_reconnect_follows_the_new_lane(edge):
    cfg = _FakeLanes(edge)
    robot = Robot(cfg).connect("livekit", timeout=3.0)
    frames: Iterator = robot.camera.frames(timeout=1.0)  # a generator: nothing runs until next()
    try:
        threading.Timer(0.2, lambda: cfg.clients[-1].push_frame(1)).start()
        assert next(frames).sequence == 1
    finally:
        robot.close()
    robot.connect("hybrid", timeout=3.0)
    try:
        # next() itself must attach to the new lane; the frame arrives while it is waiting
        threading.Timer(0.2, lambda: cfg.clients[-1].push_frame(2)).start()
        assert next(frames).sequence == 2
    finally:
        robot.close()


def test_connect_while_connected_raises_and_leaves_the_session_alone(edge):
    robot = Robot(_FakeLanes(edge)).connect("udp", timeout=3.0)
    try:
        with pytest.raises(RuntimeError, match="already connected"):
            robot.connect("livekit")
        assert robot.connected and robot.info.transport == "udp"
    finally:
        robot.close()


def test_a_mode_the_config_cannot_carry_fails_before_the_network(edge):
    cfg = ConnectionConfig(
        udp=UdpConfig(
            "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
        )
    )
    robot = Robot(cfg)
    with pytest.raises(ConnectError, match="needs the livekit slot"):
        robot.connect("livekit")
    assert edge.received == []
    assert not robot.connected


def test_a_transport_bound_robot_has_no_config_and_uses_open(robot):
    with pytest.raises(RuntimeError, match="built over a transport"):
        _ = robot.config
    with pytest.raises(RuntimeError, match="built over a transport"):
        robot.connect("udp")


def test_manager_config_end_to_end_reaches_the_fake_room(edge, manager, monkeypatch):
    """ManagerConfig → manager answers url/room/token → LiveKitTransport with a callable token
    → the room is joined with a token the user never saw."""
    from asimov_sdk import connection

    seen_tokens: list[str] = []
    real = connection.LiveKitTransport

    class Capturing(real):  # type: ignore[misc,valid-type]
        def __init__(self, url, room, *, token, **kw):
            client = FakeLiveKitClient(edge, token=token() if callable(token) else token)
            seen_tokens.append(client._token)
            super().__init__(url, room, token=token, client=client, **kw)

    monkeypatch.setattr(connection, "LiveKitTransport", Capturing)
    cfg = ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential="cred"))
    robot = Robot(cfg).connect("livekit", timeout=3.0)
    try:
        assert robot.info.transport == "livekit"
        assert seen_tokens == ["jwt-1"]  # the join uses the token the first call minted
        assert manager.requests[-1][0]["Authorization"] == "Bearer cred"
    finally:
        robot.close()
    time.sleep(0.05)
