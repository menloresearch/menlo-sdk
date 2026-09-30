"""Pre-release hardening: what the robot reports can end a drive; the store cannot be
taken over by a manager's answer or bricked by one byte; a connect that opened a session
never leaves it behind; a close() from inside a LiveKit callback still leaves the room."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from menlo.asimov import (
    ConnectionConfig,
    ManagerConfig,
    Mode,
    ProtocolMismatchError,
    Robot,
    RobotFaultedError,
    RobotStore,
    StoredRobot,
)
from menlo.asimov.transport import _livekit_client
from menlo.asimov.transport._livekit_client import _LiveKitClient
from menlo.asimov.transport.udp import UdpTransport, state_from_robot_state
from tests.conftest import put_in, route_manager_rooms_to

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
    with pytest.raises(ValueError, match=r"already saved for Asimov Manager http://10\.0\.0\.5"):
        store.put(impostor)
    kept = RobotStore(path).get("menlo-0001")
    assert kept is not None and kept.credential == "real" and kept.manager_url.endswith("0.0.5")
    # the same manager may update its own entry, and a caller who chose the name may replace
    store.put(StoredRobot("menlo-0001", "http://10.0.0.5", "rotated", room="robot-menlo-0001"))
    assert store.get("menlo-0001").credential == "rotated"  # type: ignore[union-attr]
    store.put(impostor, allow_manager_change=True)
    assert store.get("menlo-0001").credential == "evil"  # type: ignore[union-attr]


def test_a_persist_that_cannot_write_closes_the_session_it_just_opened(edge, manager, monkeypatch):
    route_manager_rooms_to(edge, monkeypatch)
    from menlo.asimov import robot as robot_module

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
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, wait=False)  # unbounded: held at 10 Hz until something ends it
    assert edge.wait_for(lambda r: len(r) >= 3, 2.0), "the keepalive is re-sending"
    edge.set_mode("damp")  # another controller's DAMP, no fault
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
    assert robot.connected and robot.get_state().faulted  # the session itself is still up


def test_a_fault_damp_during_a_waited_walk_raises_rather_than_returns(edge, robot):
    put_in(robot, edge, "move")

    def fault_soon() -> None:
        time.sleep(0.4)
        edge.set_mode("damp")
        a = edge.state.active_alerts.add()
        a.id, a.severity = 7, 0  # FALL_DETECTED, CRITICAL: a fault-DAMP

    threading.Thread(target=fault_soon, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(RobotFaultedError, match="FALL_DETECTED") as info:
        robot.set_velocity(vx=0.2, duration=5.0)  # wait=True: blocks for the walk
    assert time.monotonic() - started < 2.0, "the fault ends the wait, not the duration"
    assert info.value.sent is not None and info.value.sent.command.vx == 0.2
    assert info.value.has("faulted")
    n = len(edge.received)
    time.sleep(0.35)
    assert len(edge.received) == n, "nothing more went out after the fault"


def test_balance_after_a_fault_damp_ends_a_raw_stream_refuses_and_sends_nothing(edge, robot):
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, hold=False)  # one raw packet: nothing is latched
    edge.set_mode("damp")
    a = edge.state.active_alerts.add()
    a.id, a.severity = 7, 0  # FALL_DETECTED, CRITICAL: a fault-DAMP
    assert edge.wait_for(lambda _: robot.get_state().faulted, 2.0)
    time.sleep(0.1)
    n = len(edge.received)
    with pytest.raises(RobotFaultedError):
        robot.balance()
    time.sleep(0.2)
    assert len(edge.received) == n, "no zero velocity went to the fault-DAMPed robot"


def test_damp_from_a_state_callback_sends_and_does_not_stall_the_state(edge, robot):
    put_in(robot, edge, "move")
    returned: list[float] = []

    def on_alert(alert, change) -> None:
        if change == "raised":
            started = time.monotonic()
            robot.damp()  # wait=True by default: in a callback it must not block
            returned.append(time.monotonic() - started)

    robot.on_alert = on_alert
    a = edge.state.active_alerts.add()
    a.id, a.severity = 3, 1  # a warning: nothing latches, the callback damps
    assert edge.wait_for(lambda _: bool(returned), 2.0)
    assert returned[0] < 0.5, "damp() in a callback returns once sent"
    assert "damp" in edge.modes()
    seq = robot.get_state().sequence
    robot.wait_until(lambda s: s.sequence > seq + 5, timeout=2.0)  # state kept flowing


def test_a_waiting_verb_in_a_state_callback_is_a_runtime_error_with_nothing_sent(edge, robot):
    put_in(robot, edge, "damp")
    errors: list[BaseException] = []

    def on_state(_state) -> None:
        if errors:
            return
        n = len(edge.received)
        for call in (
            robot.stand,
            lambda: robot.wait_until(lambda s: False, timeout=1.0),
        ):
            try:
                call()
            except RuntimeError as exc:
                errors.append(exc)
        assert len(edge.received) == n

    robot.on_state = on_state
    assert edge.wait_for(lambda _: len(errors) == 2, 2.0)
    robot.on_state = None
    assert all("wait=False" in str(e) for e in errors)
    assert "stand" not in edge.modes()


def test_a_firmware_restart_releases_a_held_velocity_and_fences_a_set_joints(edge, robot):
    edge.pushing = False
    time.sleep(0.05)
    robot._on_state(_sample(edge, 5000, 2))
    robot.set_velocity(vx=0.3, wait=False)
    gen = robot._generation
    assert robot._latched is not None
    robot._on_state(_sample(edge, 5001, 2))  # still the same firmware: nothing changes
    assert robot._latched is not None and robot._generation == gen
    robot._on_state(_sample(edge, 0, 0))  # the counter restarted: a rebooted, DAMPed firmware
    assert robot.get_state().sequence == 0, "the restarted stream is followed, as before"
    assert robot._latched is None, "but the velocity latched for the old firmware is gone"
    assert robot._generation > gen, "and a running set_joints / trajectory re-send is fenced off"


def test_a_sample_arriving_while_open_is_still_running_is_handshaken(edge, monkeypatch):
    """LiveKit's open() waits for media for seconds; state that lands meanwhile must go
    through the late handshake, not be stored raw as `robot.get_state()`."""
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
            mismatched.get_state()
    finally:
        mismatched.close()

    matched = robot_over(None)
    try:
        assert matched.get_state().mode is Mode.DAMP, "handshaken in time"
        assert matched.info.dof == 25
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
    safety zero, then close the client, on the client's own loop thread."""
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
    assert lk_client.identity is None and lk_client.tracks == frozenset(), "reports gone"


def test_close_from_another_thread_is_unchanged(lk_client):
    thread = lk_client._thread
    lk_client.publish_data(b"zero", topic="asimov.command")
    lk_client.close()
    assert thread is not None and not thread.is_alive()
    room = _FakeRoom.instances[-1]
    assert room.disconnected and room.local_participant.published == [("asimov.command", b"zero")]
