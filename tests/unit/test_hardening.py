"""Pre-release hardening: what the robot reports can end a drive; the store cannot be
taken over by a manager's answer or bricked by one byte; a connect that opened a session
never leaves it behind; a close() from inside a LiveKit callback still leaves the room."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from asimov_sdk import (
    ConnectionConfig,
    ManagerConfig,
    Mode,
    ProtocolMismatchError,
    Robot,
    RobotStore,
    StoredRobot,
)
from asimov_sdk.cli import main
from asimov_sdk.transport import _livekit_client
from asimov_sdk.transport._livekit_client import _LiveKitClient
from asimov_sdk.transport.udp import UdpTransport, state_from_robot_state
from tests.conftest import route_manager_rooms_to

CRED = "eyJpZCI6ImEwY2QxNmRiIn0.secret"


# ── the store ─────────────────────────────────────────────────────────────────


def test_a_del_byte_in_a_room_name_does_not_brick_the_store(tmp_path):
    path = tmp_path / "robots.toml"
    RobotStore(path).put(StoredRobot("odd", "http://10.0.0.5", CRED, room="robot-a\x7fb"))
    again = RobotStore(path)  # one unreadable byte here used to make every Robot() fail
    saved = again.get("odd")
    assert saved is not None and saved.room == "robot-a\x7fb"


def test_an_entry_is_not_taken_over_by_another_manager_unless_named(tmp_path):
    path = tmp_path / "robots.toml"
    store = RobotStore(path)
    store.put(StoredRobot("menlo-0001", "http://10.0.0.5", "real", room="robot-menlo-0001"))
    # a second manager answers the FIRST robot's room: the key it lands on is not its own
    impostor = StoredRobot("menlo-0001", "http://192.168.9.9", "evil", room="robot-menlo-0001")
    with pytest.raises(ValueError, match=r"already saved for manager http://10\.0\.0\.5"):
        store.put(impostor)
    kept = RobotStore(path).get("menlo-0001")
    assert kept is not None and kept.credential == "real" and kept.manager_url.endswith("0.0.5")
    # the same manager may update its own entry, and a caller who chose the name may replace
    store.put(StoredRobot("menlo-0001", "http://10.0.0.5", "rotated", room="robot-menlo-0001"))
    assert store.get("menlo-0001").credential == "rotated"  # type: ignore[union-attr]
    store.put(impostor, allow_manager_change=True)
    assert store.get("menlo-0001").credential == "evil"  # type: ignore[union-attr]


def test_login_refuses_to_overwrite_another_managers_entry_without_name(manager, capsys):
    RobotStore().put(StoredRobot("menlo-0042", "http://10.9.9.9", "good", room="robot-menlo-0042"))
    assert main(["login", manager.url, "--credential", CRED]) == 1
    err = capsys.readouterr().err
    assert "already saved for manager http://10.9.9.9" in err and "--name" in err
    kept = RobotStore().get("menlo-0042")
    assert kept is not None and kept.credential == "good"
    # --name is the user choosing the key, so it may land wherever they said
    assert main(["login", manager.url, "--credential", CRED, "--name", "bench"]) == 0
    store = RobotStore()
    assert store.get("bench") is not None and store.get("menlo-0042").credential == "good"  # type: ignore[union-attr]


def test_a_persist_that_cannot_write_closes_the_session_it_just_opened(edge, manager, monkeypatch):
    route_manager_rooms_to(edge, monkeypatch)
    from asimov_sdk import robot as robot_module

    def refuse(*_a, **_k):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(robot_module.store, "persist", refuse)
    robot = Robot(ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential=CRED)))
    with pytest.raises(OSError):
        robot.connect("livekit", timeout=3.0, persist=True)
    assert not robot.connected and robot._keepalive is None, "nothing left running behind"


# ── the robot ends a drive too ────────────────────────────────────────────────


def _sample(edge, seq: int, mode: int, *, protocol: int | None = None):
    m = edge.state.__class__()
    m.CopyFrom(edge.state)
    m.sequence, m.current_mode = seq, mode
    if protocol is not None:
        m.protocol_version = protocol
    return state_from_robot_state(m, None)


def test_a_fault_damp_releases_a_held_velocity(edge, robot):
    robot.set_velocity(vx=0.2)  # unbounded: held at 10 Hz until something ends it
    assert edge.wait_for(lambda r: len(r) >= 3, 2.0), "the keepalive is re-sending"
    time.sleep(0.3)
    assert robot._latched is not None, "a plain DAMP report (no fault) does not end a drive"
    a = edge.state.active_alerts.add()
    a.id, a.severity = 7, 0  # CRITICAL; the mode is already DAMP: this is a fault-DAMP
    deadline = time.monotonic() + 1.0
    while robot._latched is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert robot._latched is None, "the fault released the hold"
    n = len(edge.received)
    time.sleep(0.35)
    assert len(edge.received) == n, "and nothing more went out, not even a zero"
    assert robot.connected and robot.state.faulted  # the session itself is still up


def test_a_firmware_restart_releases_a_held_velocity_and_fences_a_goto(edge, robot):
    edge.pushing = False
    time.sleep(0.05)
    robot._on_state(_sample(edge, 5000, 2))
    robot.set_velocity(vx=0.3)
    gen = robot._generation
    assert robot._latched is not None
    robot._on_state(_sample(edge, 5001, 2))  # still the same firmware: nothing changes
    assert robot._latched is not None and robot._generation == gen
    robot._on_state(_sample(edge, 0, 0))  # the counter restarted: a rebooted, DAMPed firmware
    assert robot.state.sequence == 0, "the restarted stream is followed, as before"
    assert robot._latched is None, "but the velocity latched for the old firmware is gone"
    assert robot._generation > gen, "and a running goto / trajectory re-send is fenced off"


def test_a_sample_arriving_while_open_is_still_running_is_handshaken(edge, monkeypatch):
    """LiveKit's open() waits for media for seconds; state that lands meanwhile must go
    through the late handshake, not be stored raw as `robot.state`."""
    edge.pushing = False
    time.sleep(0.05)

    def robot_over(protocol: int | None):
        tx = UdpTransport(
            "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
        )
        r = Robot(tx)
        real_open = tx.open

        def open_and_deliver_one_sample():
            real_open()
            r._on_state(_sample(edge, 1, 0, protocol=protocol))  # before open() returned

        monkeypatch.setattr(tx, "open", open_and_deliver_one_sample)
        r.open(require_state=False)
        return r

    mismatched = robot_over(99)
    try:
        with pytest.raises(ProtocolMismatchError):
            mismatched.state  # noqa: B018 - the read is the assertion
    finally:
        mismatched.close()

    matched = robot_over(None)
    try:
        assert matched.state.mode is Mode.DAMP and matched.info.dof == 25, "handshaken in time"
    finally:
        matched.close()


# ── LiveKit: close() from inside a callback ───────────────────────────────────


class _FakeParticipant:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def publish_data(self, payload: bytes, *, reliable: bool, topic: str) -> None:
        await asyncio.sleep(0.01)  # a round trip to the SFU
        self.published.append((topic, payload))


class _FakeRoom:
    instances: list[_FakeRoom] = []

    def __init__(self) -> None:
        self.disconnected = False
        self.local_participant = _FakeParticipant()
        _FakeRoom.instances.append(self)

    def on(self, event: str, callback) -> None:
        pass

    async def connect(self, url: str, token: str, options=None) -> None:
        pass

    async def disconnect(self) -> None:
        await asyncio.sleep(0.01)
        self.disconnected = True


class _FakeRtc:
    Room = _FakeRoom

    class RoomOptions:
        def __init__(self, **kw) -> None:
            pass


@pytest.fixture
def lk_client(monkeypatch):
    monkeypatch.setattr(_livekit_client, "_rtc", lambda: _FakeRtc)
    client = _LiveKitClient("ws://robot:7880", "robot-menlo-0042", token=lambda: "jwt")
    client.connect()
    yield client
    client.close()


def test_close_from_the_loop_thread_still_sends_the_zero_and_leaves_the_room(lk_client):
    """What Robot.close() does from an on_state callback on the livekit lane: queue the
    safety zero, then close the client — on the client's own loop thread."""
    loop, thread = lk_client._loop, lk_client._thread
    assert loop is not None and thread is not None and thread.is_alive()
    took: list[float] = []
    done = threading.Event()

    def from_a_state_callback() -> None:
        lk_client.publish_data(b"zero", topic="asimov.command")
        t0 = time.monotonic()
        lk_client.close()
        took.append(time.monotonic() - t0)
        done.set()

    loop.call_soon_threadsafe(from_a_state_callback)
    assert done.wait(3.0)
    thread.join(3.0)
    assert not thread.is_alive(), "the loop stopped itself once the leave was done"
    room = _FakeRoom.instances[-1]
    assert room.disconnected, "the room was left"
    assert room.local_participant.published == [("asimov.command", b"zero")], "zero went out"
    assert took[0] < 1.0, f"close() did not stall on its own loop ({took[0]:.2f}s)"
    assert not lk_client.connected


def test_close_from_another_thread_is_unchanged(lk_client):
    thread = lk_client._thread
    lk_client.publish_data(b"zero", topic="asimov.command")
    lk_client.close()
    assert thread is not None and not thread.is_alive()
    room = _FakeRoom.instances[-1]
    assert room.disconnected and room.local_participant.published == [("asimov.command", b"zero")]
