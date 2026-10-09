"""Pre-release hardening: what the robot reports can end a drive; the store cannot be
taken over by a manager's answer or bricked by one byte; a connect that opened a session
never leaves it behind; a close() from inside a LiveKit callback still leaves the room."""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import time

import pytest

from menlo.asimov import (
    AudioChunk,
    Battery,
    BatteryProtection,
    ConnectionConfig,
    Frame,
    Joint,
    LinkLostError,
    ManagerConfig,
    Mode,
    NotConnectedError,
    NotReadyError,
    ProtocolMismatchError,
    Robot,
    RobotFaultedError,
    RobotStore,
    State,
    StateStaleError,
    StoredRobot,
    UdpConfig,
    WaitTimeoutError,
)
from menlo.asimov._command import Velocity
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


def test_balance_after_a_fault_damp_ends_a_raw_stream_sends_zero_and_raises_the_fault(edge, robot):
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, hold=False)  # one raw packet: nothing is latched
    edge.set_mode("damp")
    a = edge.state.active_alerts.add()
    a.id, a.severity = 7, 0  # FALL_DETECTED, CRITICAL: a fault-DAMP
    assert edge.wait_for(lambda _: robot.get_state().faulted, 2.0)
    time.sleep(0.1)
    with pytest.raises(RobotFaultedError) as info:
        robot.balance()
    assert info.value.sent is not None and info.value.sent.command == Velocity()
    assert edge.wait_for(lambda _: edge.velocities()[-1] == (0.0, 0.0, 0.0), 2.0)


def test_damp_from_a_state_callback_sends_and_does_not_stall_the_state(edge, robot):
    """An alert callback sends DAMP without blocking the reader or later state frames."""
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
    # UDP send completion does not mean the fake edge's receiver has consumed the packet.
    assert edge.wait_for(lambda _: "damp" in edge.modes(), 2.0)
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


# ── a transport in memory: the test decides every sample and sees every command ───────


def _mem_sample(mode: Mode = Mode.MOVE, sequence: int = 10, **changes):
    state = State(
        mode=mode,
        joints=tuple(Joint(f"joint{i}", 0.0, 0.0, 0.0, 30.0) for i in range(3)),
        gravity=(0.0, 0.0, -1.0),
        gyro=None,
        quat=None,
        error_flags=0,
        alerts=(),
        sequence=sequence,
        fw_timestamp_us=1_000_000 + sequence * 5_000,
        protocol_version=1,
    )
    return dataclasses.replace(state, **changes)


class _MemoryTransport:
    kind = "livekit"
    endpoint = "memory"
    capabilities = frozenset({"drive", "state", "camera", "microphone"})
    default_outcome_timeout = 0.01

    def __init__(self) -> None:
        self.state = _mem_sample()
        self.state_callbacks: list = []
        self.frame_callbacks: list = []
        self.audio_callbacks: list = []
        self.commands: list = []
        self.after_send = None
        self.closed = False

    def subscribe_state(self, callback) -> None:
        self.state_callbacks.append(callback)

    def subscribe_outcome(self, callback) -> None:
        pass

    def subscribe_controller_change(self, callback) -> None:
        pass

    def subscribe_frames(self, callback) -> None:
        self.frame_callbacks.append(callback)

    def subscribe_audio(self, callback) -> None:
        self.audio_callbacks.append(callback)

    def open(self) -> None:
        self.emit(dataclasses.replace(self.state, received_at=time.monotonic()))

    def close(self) -> None:
        self.closed = True

    def emit(self, state) -> None:
        self.state = state
        for callback in tuple(self.state_callbacks):
            callback(state)

    def send(self, command) -> int:
        self.commands.append(command)
        if self.after_send is not None:
            self.after_send(command)
        return len(self.commands)


@pytest.fixture
def mem():
    transport = _MemoryTransport()
    robot = Robot(transport)
    robot.open()
    try:
        yield robot, transport
    finally:
        robot.close()


@pytest.mark.parametrize("hold", [True, False])
@pytest.mark.parametrize("mode", [Mode.DAMP, Mode.STAND])
def test_balance_outside_move_sends_zero_whatever_velocity_was_sent(mem, hold, mode):
    robot, transport = mem
    robot.set_velocity(vx=0.2, hold=hold, wait=False)
    low = Battery(40, 0, 5, 30, BatteryProtection(0))  # 5 %: the SDK does not guard on it
    transport.emit(_mem_sample(mode, sequence=11, battery=low))
    n = len(transport.commands)
    robot.balance(wait=False)
    assert transport.commands[n:] == [Velocity()], "one zero velocity, in any robot mode"
    assert robot._latched is None, "and the old velocity is not re-sent"


def test_set_joints_with_bad_gains_leaves_the_held_velocity_for_close_to_zero(mem):
    robot, transport = mem
    robot.set_velocity(vx=0.2, wait=False)
    with pytest.raises(ValueError, match="both kp and kd"):
        robot.set_joints([0.1, 0.0, 0.0], kp=[1.0, 1.0, 1.0], wait=False)
    assert robot._latched is not None, "a rejected call changes nothing"
    robot.close()
    assert transport.commands[-1] == Velocity(), "close() zeroed the velocity still held"


def test_a_repeated_sample_is_not_fresh_and_does_not_arm(mem):
    robot, transport = mem
    transport.emit(_mem_sample(Mode.DAMP, sequence=11))
    original = _mem_sample(Mode.STAND, sequence=12, received_at=time.monotonic() - 0.6)
    transport.emit(original)
    for _ in range(6):  # the same datagram decoded again, each with a new arrival time
        transport.emit(dataclasses.replace(original, received_at=time.monotonic()))
    assert robot.get_state().received_at == original.received_at, "the copies were dropped"
    assert robot.armed is False, "0.6 s of copies is not 0.5 s of observed upright STAND"
    with pytest.raises(StateStaleError):
        robot.wait_until(lambda _s: True, stale_after=0.05, timeout=0)


def test_an_unstamped_stream_is_still_followed():
    transport = _MemoryTransport()
    transport.state = _mem_sample(Mode.STAND, sequence=0, fw_timestamp_us=0)
    with Robot(transport) as robot:
        robot.open()
        later = dataclasses.replace(transport.state, received_at=time.monotonic() + 0.01)
        transport.emit(later)
        assert robot.get_state() is later, "no stamp to compare: every sample is taken"


@pytest.mark.parametrize("end", ["fault", "closed", "lost"])
def test_a_waited_zero_velocity_ends_with_the_session_error(mem, end):
    robot, transport = mem
    ended = threading.Event()

    def end_it() -> None:
        ended.wait(1.0)
        if end == "fault":
            transport.emit(_mem_sample(Mode.FAULT_DAMP, sequence=11, error_flags=1))
        elif end == "closed":
            robot.close()
        else:
            robot._mark_link_lost(LinkLostError("the link went quiet"))

    worker = threading.Thread(target=end_it)
    worker.start()
    robot._on_sent = lambda _sent: ended.set()  # ends the session once the zero is out
    expected = {"fault": RobotFaultedError, "closed": NotConnectedError, "lost": LinkLostError}
    started = time.monotonic()
    try:
        with pytest.raises(expected[end]):
            robot.set_velocity(duration=5.0)  # zero speeds: nothing held, a waited 5 s
    finally:
        worker.join()
    assert time.monotonic() - started < 2.0, "it ended with the session, not the duration"


def test_a_waited_zero_velocity_returns_when_another_verb_takes_over(mem):
    robot, _ = mem
    threading.Timer(0.05, lambda: robot.set_velocity(vx=0.1, wait=False)).start()
    started = time.monotonic()
    robot.set_velocity(duration=5.0)
    assert time.monotonic() - started < 2.0


def test_a_waited_velocity_sent_into_a_reported_fault_raises_it_and_sends_no_zero():
    transport = _MemoryTransport()
    transport.state = _mem_sample(Mode.FAULT_DAMP, error_flags=1)  # fresh, before the call
    with Robot(transport) as robot:
        robot.open()
        with pytest.raises(RobotFaultedError) as caught:
            robot.set_velocity(vx=0.2, duration=0.01)
        assert caught.value.sent is not None
        assert caught.value.sent.command == Velocity(0.2, 0.0, 0.0), "this command's Sent"
        assert robot._latched is None, "the hold ended when it began"
        # The next sample comes after the hold's deadline and any keepalive tick.
        transport.emit(_mem_sample(Mode.FAULT_DAMP, sequence=11, error_flags=1))
        assert transport.commands == [Velocity(0.2, 0.0, 0.0)], "sent once, and no zero"


def test_a_later_hold_does_not_erase_the_fault_that_ended_an_earlier_one(mem):
    robot, transport = mem
    original = robot._await_hold
    entered, release = threading.Event(), threading.Event()

    def paused(done, duration, sent) -> None:
        entered.set()
        release.wait(5.0)
        original(done, duration, sent)

    robot._await_hold = paused
    results: list[object] = []

    def first() -> None:
        try:
            results.append(robot.set_velocity(vx=0.2, duration=5.0))
        except RobotFaultedError as exc:
            results.append(exc)

    caller = threading.Thread(target=first)
    caller.start()
    try:
        assert entered.wait(5.0)
        transport.emit(_mem_sample(Mode.FAULT_DAMP, sequence=11, error_flags=1))
        robot.set_velocity(vx=0.1, duration=1.0, wait=False)  # a second hold, allowed
        transport.emit(_mem_sample(Mode.FAULT_DAMP, sequence=12, error_flags=1))
    finally:
        release.set()
        caller.join(5.0)
    assert len(results) == 1 and isinstance(results[0], RobotFaultedError), results
    assert results[0].sent is not None and results[0].sent.command == Velocity(0.2, 0.0, 0.0)


def test_a_waited_hold_whose_keepalive_stalls_raises_and_sends_the_zero(mem):
    robot, transport = mem
    stalled, release = threading.Event(), threading.Event()

    def on_sent(sent) -> None:  # a recording hook that blocks the keepalive's re-send
        if threading.current_thread().name == "menlo-sdk-keepalive" and not sent.command.is_zero:
            stalled.set()
            release.wait(5.0)

    robot._on_sent = on_sent
    try:
        with pytest.raises(WaitTimeoutError, match="keepalive thread stalled"):
            robot.set_velocity(vx=0.2, duration=0.2)
        assert stalled.is_set()
        assert robot._latched is None, "the hold is over"
        assert transport.commands[-1] == Velocity(), "and its zero went out"
    finally:
        robot._on_sent = None
        release.set()


def test_a_stalled_recording_write_does_not_hold_up_the_stall_zero(mem):
    robot, transport = mem
    write_lock, release = threading.Lock(), threading.Event()

    def on_sent(sent) -> None:  # a recording whose write blocks while holding its lock
        with write_lock:
            if threading.current_thread().name == "menlo-sdk-keepalive":
                release.wait(10.0)

    robot._on_sent = on_sent
    try:
        started = time.monotonic()
        with pytest.raises(WaitTimeoutError, match="keepalive thread stalled"):
            robot.set_velocity(vx=0.2, duration=0.2)
        assert time.monotonic() - started < 3.0, "the wait ends at its budget"
        assert transport.commands[-1] == Velocity(), "and its zero went out"
    finally:
        robot._on_sent = None
        release.set()


@pytest.mark.parametrize("hold", [True, False])
def test_balance_that_refuses_after_a_velocity_leaves_close_its_zero(edge, robot, hold):
    put_in(robot, edge, "move")
    if hold:
        robot.set_velocity(vx=0.3, wait=False)
    else:
        robot.set_velocity(vx=0.3, hold=False)
    edge.set_mode("damp")  # another controller's DAMP, no fault
    assert edge.wait_for(lambda _: robot.get_state().mode is Mode.DAMP, 2.0)
    edge.pushing = False  # and then no live state: the one refusal
    time.sleep(0.7)
    n = len(edge.received)
    with pytest.raises(NotReadyError) as info:
        robot.balance(timeout=0.1)
    assert info.value.has("stale_state")
    assert len(edge.received) == n, "the refusal sent nothing"
    robot.close()
    assert edge.wait_for(lambda r: len(r) > n, 2.0)
    assert edge.velocities()[-1] == (0.0, 0.0, 0.0), "close() sent the zero it owed"


def test_a_connect_while_another_is_opening_is_refused_and_nothing_leaks(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Paused(_MemoryTransport):
        def open(self) -> None:
            entered.set()
            assert release.wait(2.0)
            super().open()

    first, second = Paused(), _MemoryTransport()
    made = iter((first, second))
    monkeypatch.setattr(ConnectionConfig, "transport_for", lambda *_a, **_k: next(made))
    robot = Robot(ConnectionConfig(udp=UdpConfig("unused")))
    errors: list[BaseException] = []

    def connect_first() -> None:
        try:
            robot.connect("udp", timeout=1.0, persist=False)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=connect_first)
    worker.start()
    try:
        assert entered.wait(1.0)
        with pytest.raises(RuntimeError, match="still running"):
            robot.connect("udp", timeout=1.0, persist=False)
        release.set()
        worker.join(2.0)
        assert not errors and robot._transport is first, "the first connect owns the session"
        keepalive = robot._keepalive
        robot.close()
        assert first.closed and keepalive is not None and not keepalive.is_alive()
        assert not second.state_callbacks, "the refused connect built nothing"
    finally:
        release.set()
        worker.join(2.0)
        robot.close()


def test_a_udp_reopen_from_a_state_callback_ends_the_old_reader(monkeypatch):
    import queue

    from menlo.asimov.transport import udp
    from menlo.asimov.transport._wire import _pb

    class Socket:
        def __init__(self) -> None:
            self.incoming: queue.Queue[bytes] = queue.Queue()
            self.closed = False
            self.bad_reads = 0

        def bind(self, address) -> None:
            pass

        def settimeout(self, timeout) -> None:
            pass

        def recvfrom(self, size):
            if self.closed:
                self.bad_reads += 1
                raise OSError(9, "Bad file descriptor")
            try:
                return self.incoming.get(timeout=0.01), ("127.0.0.1", 8851)
            except queue.Empty:
                raise TimeoutError from None

        def close(self) -> None:
            self.closed = True

    old_socket, new_socket = Socket(), Socket()
    sockets = iter((old_socket, new_socket))
    monkeypatch.setattr(udp.socket, "socket", lambda *_a, **_k: next(sockets))
    monkeypatch.setattr(udp.socket, "gethostbyname", lambda host: "127.0.0.1")
    _, common, state_pb = _pb()
    packet = state_pb.RobotState(
        protocol_version=1,
        current_mode=common.CONTROL_MODE_MOVE,
        sequence=1,
        timestamp_us=1000,
        joint_pos=[0.0, 0.0, 0.0],
    )
    old_socket.incoming.put(packet.SerializeToString())
    transport = udp.UdpTransport("127.0.0.1")
    robot = Robot(transport)
    robot.open(timeout=1.0)
    old_reader = transport._reader
    assert old_reader is not None
    reopened = threading.Event()

    def reconnect(_state) -> None:
        robot.on_state = None
        robot.close()
        robot.open(require_state=False)
        reopened.set()

    robot.on_state = reconnect
    packet.sequence = 2
    old_socket.incoming.put(packet.SerializeToString())
    try:
        assert reopened.wait(1.0)
        old_reader.join(1.0)
        assert not old_reader.is_alive(), "the old session's reader ended with its session"
        assert old_socket.bad_reads == 0, "and never spun on its closed socket"
        assert transport._reader is not None and transport._reader.is_alive()
    finally:
        robot.close()


def test_stand_does_not_count_another_controllers_move_as_standing(mem):
    robot, transport = mem
    transport.emit(_mem_sample(Mode.DAMP, sequence=11))
    transport.after_send = lambda _c: transport.emit(_mem_sample(Mode.MOVE, sequence=12))
    with pytest.raises(WaitTimeoutError, match="still reports MOVE"):
        robot.stand(timeout=0)


def test_a_reopen_on_the_same_transport_forgets_the_last_sessions_media(mem):
    robot, transport = mem
    assert robot.camera.latest() is None and robot.microphone.latest() is None  # attaches
    transport.frame_callbacks[0](Frame(width=1, height=1, encoding="rgb8", data=b"\0\0\0"))
    transport.audio_callbacks[0](AudioChunk(16_000, 1, 1, "pcm_s16le", b"\0\0"))
    robot.close()
    robot.open()
    assert robot.camera.latest() is None, "no frame from the previous session"
    with pytest.raises(WaitTimeoutError):
        next(robot.microphone.chunks(timeout=0))  # no audio from it either
    assert len(transport.frame_callbacks) == 1 and len(transport.audio_callbacks) == 1


def test_leaving_the_room_lets_queued_speaker_audio_play_out_first(lk_client):
    room = _FakeRoom.instances[-1]
    seen: list[str] = []

    class Source:  # LiveKit's AudioSource: capture_frame() queues, wait_for_playout() drains
        async def wait_for_playout(self) -> None:
            await asyncio.sleep(0.05)
            seen.append("left before playout" if room.disconnected else "played out")

    lk_client._source = Source()
    lk_client.close()
    assert room.disconnected and seen == ["played out"]


def test_a_verb_in_a_state_callback_while_recording_still_marks_the_callback(mem):
    robot, transport = mem
    robot._on_sent = lambda _sent: None  # what robot.record() installs
    after: list[BaseException] = []

    def on_state(_state) -> None:
        robot.damp()  # its Sent goes through the recording hook, itself a callback
        try:
            robot.wait_until(lambda _s: False, timeout=0.2)
        except BaseException as exc:
            after.append(exc)

    robot.on_state = on_state
    transport.emit(_mem_sample(Mode.MOVE, sequence=11))
    robot.on_state = None
    assert len(after) == 1 and isinstance(after[0], RuntimeError), after


class _Silent(_MemoryTransport):
    def open(self) -> None:  # the robot has not reported when the session opens
        pass


def test_a_checked_verb_waits_for_the_first_sample_of_a_session_opened_without_state():
    transport = _Silent()
    with Robot(transport) as robot:
        robot.open(require_state=False)
        original = robot.preflight

        def first_check_then_sample(action="move"):
            check = original(action)
            if check.has("no_state"):  # the verb is waiting: the first sample arrives now
                transport.emit(_mem_sample(Mode.DAMP, received_at=time.monotonic()))
            return check

        robot.preflight = first_check_then_sample
        sent = robot.stand(wait=False, timeout=5.0)
        assert sent.sequence == 1 and len(transport.commands) == 1, "sent once the robot reported"


def test_a_checked_verb_without_a_first_sample_raises_no_state_with_nothing_sent():
    transport = _Silent()
    with Robot(transport) as robot:
        robot.open(require_state=False)
        with pytest.raises(NotReadyError) as info:
            robot.set_velocity(vx=0.1, wait=False, timeout=0.05)
        assert info.value.has("no_state") and transport.commands == []


@pytest.mark.parametrize("end", ["closed", "lost"])
def test_a_checked_verb_on_an_ended_session_raises_its_own_error_at_once(mem, end):
    robot, transport = mem
    if end == "closed":
        robot.close()
    else:
        robot._mark_link_lost(LinkLostError("the link went quiet"))
    n = len(transport.commands)
    expected = NotConnectedError if end == "closed" else LinkLostError
    started = time.monotonic()
    with pytest.raises(expected):
        robot.stand(wait=False, timeout=5.0)
    assert time.monotonic() - started < 1.0, "at once, not after the timeout"
    assert len(transport.commands) == n
