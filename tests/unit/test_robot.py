"""`Robot` against a fake edge that speaks the real wire.

Every test here is a promise a script author relies on. They run without a robot; the
wire is bare protobuf over loopback UDP, so a fake edge in a thread exercises the whole
path: encoding, the hold, the waits, the error model.
"""

from __future__ import annotations

import threading
import time

import pytest

from menlo.asimov import (
    ConnectError,
    LinkLostError,
    MenloError,
    Mode,
    NotConnectedError,
    ProtocolMismatchError,
    Refusal,
    Refused,
    Robot,
    RobotFaultedError,
    StateStaleError,
    Unknown,
    Velocity,
    WaitTimeoutError,
)
from menlo.asimov.robot import KEEPALIVE_HZ
from tests.conftest import connect_udp, put_in

# ── connect ───────────────────────────────────────────────────────────────────


def test_a_waited_hold_needs_a_duration_and_ends_early_when_superseded(edge, robot):
    put_in(robot, edge, "move")
    with pytest.raises(ValueError, match="needs a duration"):
        robot.set_velocity(vx=0.1, wait=True)
    threading.Timer(0.15, robot.balance).start()
    started = time.monotonic()
    robot.set_velocity(vx=0.2, duration=5.0, wait=True)  # another verb ends it
    assert time.monotonic() - started < 1.0
    assert edge.wait_for(lambda rx: edge.velocities()[-1:] == [(0.0, 0.0, 0.0)])
    # a zero velocity holds nothing; wait=True just stands for the time
    started = time.monotonic()
    robot.set_velocity(0.0, 0.0, 0.0, duration=0.2, wait=True)
    assert 0.2 <= time.monotonic() - started < 0.6


def test_closing_during_a_waited_hold_raises_instead_of_pretending_it_finished(edge, robot):
    put_in(robot, edge, "move")
    threading.Timer(0.1, robot.close).start()
    with pytest.raises(NotConnectedError, match="closed before the hold ended"):
        robot.set_velocity(vx=0.2, duration=5.0, wait=True)
    assert edge.wait_for(lambda rx: edge.velocities()[-1:] == [(0.0, 0.0, 0.0)])


def test_connect_waits_for_the_first_state_and_describes_the_robot(edge, robot):
    assert robot.connected
    assert robot.info.dof == 25
    assert robot.info.transport == "udp"
    assert robot.info.joint_names is not None and robot.info.joint_names[3] == "L_Knee"
    assert robot.get_state().mode is Mode.DAMP
    assert robot.get_state().upright is True


def test_connect_refuses_when_nobody_answers():
    """A UDP socket that hears nothing is talking to nobody. Say so, and say the fix."""
    with pytest.raises(ConnectError) as exc:
        connect_udp("127.0.0.1", command_port=1, state_bind=("127.0.0.1", 0), timeout=0.3)
    assert "udp-control" in str(exc.value)


def test_connect_refuses_a_protocol_version_it_was_not_built_for(edge):
    edge.state.protocol_version = 99
    with pytest.raises(ProtocolMismatchError) as exc:
        connect_udp(
            "127.0.0.1",
            command_port=edge.command_port,
            state_bind=("127.0.0.1", edge.state_port),
            timeout=2,
        )
    assert exc.value.observed == 99 and exc.value.expected == 1
    r = connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=2,
        allow_version_skew=True,
    )
    r.close()


# ── verbs ─────────────────────────────────────────────────────────────────────


def test_stand_and_damp_are_sent_once_and_encoded_as_modes(edge, robot):
    robot.stand(wait=False)
    robot.damp()
    assert edge.wait_for(lambda r: len(r) >= 2)
    time.sleep(3 / KEEPALIVE_HZ)
    assert edge.modes() == ["stand", "damp"], "a posture is an event, never re-sent"


def test_set_velocity_is_held_at_the_keepalive_rate(edge, robot):
    """The hold is what keeps the edge's 2 s watchdog from zero-STANDing the robot while
    the script is off doing vision."""
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, vyaw=0.1, wait=False)
    time.sleep(4 / KEEPALIVE_HZ)
    vels = edge.velocities()
    assert len(vels) >= 3, vels
    assert all(v == (0.2, 0.0, 0.1) for v in vels), "a repeat must not change the command"
    # Every velocity datagram is mode=MOVE + policy, the shape the edge's own connectors send.
    c = next(c for c in edge.received if c.HasField("policy"))
    assert c.mode == 2 and c.protocol_version == 1 and c.sequence >= 1 and c.timestamp_us > 0


def test_velocity_is_clamped_and_the_clamp_is_visible(edge, robot):
    put_in(robot, edge, "move")
    sent = robot.set_velocity(vx=20.0, vyaw=-99.0, wait=False)
    assert sent.clamped is True
    assert sent.command == Velocity(0.4, 0.0, -0.8)
    assert edge.wait_for(lambda r: any(c.HasField("policy") for c in r))
    assert edge.velocities()[-1] == (0.4, 0.0, -0.8)
    assert robot.set_velocity(vx=0.1, wait=False).clamped is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_velocity_is_a_caller_bug_not_a_command(edge, robot, bad):
    """NaN sails through min/max. It has to be refused before it exists as a command."""
    with pytest.raises(ValueError):
        robot.set_velocity(vx=bad, wait=False)
    time.sleep(0.05)
    assert edge.velocities() == []


def test_a_bounded_hold_ends_with_a_zero(edge, robot):
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.3, duration=0.25, wait=False)
    time.sleep(0.6)
    vels = edge.velocities()
    assert (0.3, 0.0, 0.0) in vels
    assert vels[-1] == (0.0, 0.0, 0.0), "the hold must end with an explicit zero, not silence"
    # ... and stay ended: no more datagrams after the zero.
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n


def test_stop_and_a_mode_command_end_the_hold(edge, robot):
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, wait=False)
    robot.balance()
    assert edge.wait_for(lambda r: len(edge.velocities()) >= 2)  # 0.2 then the zero
    n_after_stop = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n_after_stop, "zero is not re-sent"
    robot.set_velocity(vx=0.2, wait=False)
    robot.damp(wait=False)
    assert edge.wait_for(lambda r: len(edge.modes()) >= 1)
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert edge.velocities()[-1] == (0.2, 0.0, 0.0) and len(edge.received) == n


def test_a_superseded_velocity_never_reaches_the_robot(edge, robot):
    """The classic race: the keepalive read v1, the caller sent v2, the keepalive then
    transmitted the stale v1. Generation counting closes it."""
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.2, wait=False)
    stale_gen = robot._generation
    robot.balance()
    assert edge.wait_for(lambda r: len(edge.velocities()) >= 2)  # both real sends landed
    before = len(edge.received)
    late = robot._send("set_velocity", Velocity(0.2), stale_gen)  # the keepalive's late send
    assert late.sequence == -1, "a superseded send is reported as such, not transmitted"
    time.sleep(0.05)
    assert len(edge.received) == before


def test_trajectory_length_must_match_the_robot(edge, robot):
    put_in(robot, edge, "move")
    with pytest.raises(ValueError):
        robot.trajectory([0.0] * 24)
    robot.trajectory([0.05] * 25, kp=[50.0] * 25, kd=[2.0] * 25)
    assert edge.wait_for(lambda r: any(c.HasField("all_trajectory") for c in r))
    c = next(c for c in edge.received if c.HasField("all_trajectory"))
    assert len(c.all_trajectory.positions) == 25 and len(c.all_trajectory.kp) == 25


# ── outcomes ──────────────────────────────────────────────────────────────────


def test_outcomes_are_unknown_until_the_edge_reports_them(edge, robot):
    sent = robot.stand(wait=False)
    assert sent.outcome is None
    o = sent.wait_outcome(timeout=0.05)
    assert isinstance(o, Unknown) and o.sequence == sent.sequence
    assert sent.require(timeout=0.05) == o  # unknown is not a failure by default
    from menlo.asimov import OutcomeUnknownError

    with pytest.raises(OutcomeUnknownError):
        sent.require(timeout=0.05, unknown_ok=False)


def test_a_delivered_refusal_resolves_the_handle_and_the_callback(edge, robot):
    put_in(robot, edge, "move")
    seen = []
    robot.on_refused = seen.append
    sent = robot.set_velocity(vx=0.2, wait=False)
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FW_DAMPED, "firmware is DAMPed"))
    assert isinstance(sent.outcome, Refused) and sent.outcome.reason is Refusal.FW_DAMPED
    assert sent.outcome.reason.retryable is False
    assert seen and seen[0].sequence == sent.sequence
    assert [r.sequence for r in robot.outcomes()] == [sent.sequence]
    from menlo.asimov import CommandRefusedError

    with pytest.raises(CommandRefusedError):
        sent.require()


def test_an_unrecognised_refusal_is_still_a_refusal():
    assert Refusal.from_wire(4242) is Refusal.UNRECOGNIZED
    assert Refusal.from_wire(1) is Refusal.FW_DAMPED


# ── waits ─────────────────────────────────────────────────────────────────────


def test_wait_until_follows_the_robots_own_report(edge, robot):
    robot.stand(wait=False)

    def flip():
        time.sleep(0.2)
        edge.set_mode("stand")

    import threading

    threading.Thread(target=flip, daemon=True).start()
    s = robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=2.0)
    assert s.mode is Mode.STAND


def test_wait_times_out_with_a_typed_error_that_is_also_a_TimeoutError(edge, robot):
    with pytest.raises(WaitTimeoutError) as exc:
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=0.2)
    assert isinstance(exc.value, TimeoutError) and isinstance(exc.value, MenloError)
    assert "robot mode DAMP" in str(exc.value) and exc.value.last is not None


def test_wait_refuses_to_succeed_on_a_state_that_stopped_updating(edge, robot):
    """THE regression this design exists for: a frozen snapshot must never satisfy a wait."""
    edge.pushing = False
    time.sleep(0.4)
    with pytest.raises(StateStaleError) as exc:
        robot.wait_until(lambda s: True, timeout=1.0, stale_after=0.3)
    assert exc.value.last is not None and exc.value.last.age_s > 0.3


def test_wait_gives_up_early_on_a_fault_damp(edge, robot):
    a = edge.state.active_alerts.add()
    a.id, a.severity = 7, 0  # severity 0 == CRITICAL on the firmware's scale
    time.sleep(0.05)
    with pytest.raises(RobotFaultedError) as exc:
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=2.0)
    assert exc.value.state.faulted is True
    assert exc.value.state.alerts[0].critical is True


def test_wait_stops_when_the_pending_mode_command_is_refused(edge, robot):
    from menlo.asimov import CommandRefusedError

    sent = robot.stand(wait=False)
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    with pytest.raises(CommandRefusedError):
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=2.0)


# ── liveness & lifecycle ──────────────────────────────────────────────────────


def test_link_lost_when_state_goes_silent(edge, robot):
    robot.link_timeout = 0.3
    lost = []
    robot.on_link_lost = lost.append
    edge.pushing = False
    time.sleep(0.8)
    assert not robot.connected and lost
    with pytest.raises(LinkLostError):
        robot.set_velocity(vx=0.1, wait=False)


def test_close_zeroes_a_held_velocity_then_stops_talking(edge, robot):
    put_in(robot, edge, "move")
    robot.set_velocity(vx=0.3, wait=False)
    assert edge.wait_for(lambda r: any(c.HasField("policy") for c in r))
    robot.close()
    assert edge.wait_for(lambda r: any(c.HasField("policy") and c.policy.vx == 0.0 for c in r))
    assert edge.velocities()[-1] == (0.0, 0.0, 0.0)
    assert "damp" not in edge.modes(), "close() is not an emergency stop"
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n
    robot.close()  # idempotent
    with pytest.raises(NotConnectedError):
        robot.stand(wait=False)


def test_close_without_a_held_velocity_sends_nothing(edge, robot):
    robot.stand(wait=False)
    assert edge.wait_for(lambda r: len(r) >= 1)
    n = len(edge.received)
    robot.close()
    time.sleep(0.05)
    assert len(edge.received) == n, "a standing robot gets no gratuitous zero-velocity"


def test_with_block_closes_and_the_state_names_joints(edge):
    with connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=2,
    ) as r:
        assert r.get_state().joint("L_Knee").pos == 0.0
        with pytest.raises(KeyError):
            r.get_state().joint("left_knee")
        assert r.info.joint_index("Waist_Yaw") == 22
    assert not r.connected


# ── concurrency, liveness, and trust in the state stream ─────────────────────


def test_damp_is_never_dropped_by_a_racing_set_velocity(edge, robot):
    """A verb the caller issued must go out even while another thread drives. The
    generation fence is for the keepalive's re-sends only."""
    import sys
    import threading

    put_in(robot, edge, "move")  # this fake robot stays in MOVE: the drive keeps driving
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        stop = threading.Event()
        drives: list[int] = []
        errors: list[BaseException] = []

        def drive() -> None:
            try:
                while not stop.is_set():
                    robot.set_velocity(vx=0.1, wait=False)
                    drives.append(1)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=drive, daemon=True)
        t.start()
        try:
            dropped = sum(robot.damp(wait=False).sequence == -1 for _ in range(1500))
        finally:
            stop.set()
            t.join(timeout=2.0)
    finally:
        sys.setswitchinterval(old)
    assert dropped == 0, f"{dropped} damp() calls were dropped as 'superseded'"
    assert drives and errors == [], f"the racing drive ran and never raised: {errors}"


def test_wait_until_survives_the_pending_table_churning_under_it(edge, robot):
    import sys
    import threading

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        stop = threading.Event()

        def churn() -> None:
            while not stop.is_set():
                robot.stand(wait=False)  # every send inserts into (and trims) the pending table

        t = threading.Thread(target=churn, daemon=True)
        t.start()
        try:
            # Must end with the promised typed error, never RuntimeError from a dict
            # mutated during iteration.
            with pytest.raises(WaitTimeoutError):
                robot.wait_until(lambda s: False, timeout=0.6, poll=0.0)
        finally:
            stop.set()
            t.join(timeout=2.0)
    finally:
        sys.setswitchinterval(old)


def test_close_from_the_link_lost_callback_really_closes(edge, robot):
    """`on_link_lost` runs on the keepalive thread; the obvious handler closes the robot.
    That must not blow up on a self-join and leave the socket (and port) held."""
    robot.link_timeout = 0.3
    robot.on_link_lost = lambda exc: robot.close()
    edge.pushing = False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and robot._tx._sock is not None:
        time.sleep(0.02)
    assert robot._tx._sock is None, "transport still open after close() from the callback"
    assert robot._keepalive is None and not robot.connected


def test_link_lost_zeroes_a_held_velocity(edge, robot):
    """State went quiet but the command path may still reach the edge: the last frame the
    robot hears from us must be zero, not the velocity we were holding."""
    put_in(robot, edge, "move")
    robot.link_timeout = 0.3
    robot.set_velocity(vx=0.3, wait=False)
    assert edge.wait_for(lambda r: any(c.HasField("policy") and c.policy.vx > 0 for c in r))
    edge.pushing = False
    time.sleep(0.8)
    assert not robot.connected
    assert edge.velocities()[-1] == (0.0, 0.0, 0.0)
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n, "nothing may follow the zero"


def _spam_state_port(port: int, payload: bytes, stop) -> None:
    import socket

    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while not stop.is_set():
        out.sendto(payload, ("127.0.0.1", port))
        time.sleep(0.01)
    out.close()


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty datagram decodes as a default RobotState"),
        pytest.param(
            __import__("menlo.asimov._proto", fromlist=["load"])
            .load()
            .state.RobotState(protocol_version=1, joint_pos=[0.0, 0.0, 0.0])
            .SerializeToString(),
            id="right protocol, wrong robot (3 joints)",
        ),
    ],
)
def test_a_foreign_state_datagram_does_not_keep_the_link_alive(edge, robot, payload):
    import threading

    robot.link_timeout = 0.3
    edge.pushing = False
    stop = threading.Event()
    t = threading.Thread(target=_spam_state_port, args=(edge.state_port, payload, stop))
    t.start()
    try:
        time.sleep(0.9)
        assert not robot.connected, "foreign datagrams refreshed liveness"
        assert len(robot.get_state().joints) == 25, "a foreign sample became robot.get_state()"
    finally:
        stop.set()
        t.join(timeout=1.0)


def test_a_refused_stand_does_not_poison_a_later_drive(edge, robot):
    from menlo.asimov import CommandRefusedError

    sent = robot.stand(wait=False)
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    with pytest.raises(CommandRefusedError):
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=1.0)
    edge.set_mode("move")
    robot.set_velocity(vx=0.1, wait=False)  # a new verb is in force; the old refusal is history
    assert robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=2.0).mode is Mode.MOVE


def test_a_fault_in_an_unknown_mode_still_fails_fast(edge, robot):
    edge.state.current_mode = 99
    edge.state.error_flags = 0x4
    with pytest.raises(RobotFaultedError):
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=3.0)


def test_the_host_is_resolved_once_at_open(edge):
    from menlo.asimov.transport.udp import UdpTransport
    from tests.conftest import _free_port

    tx = UdpTransport(
        "localhost", command_port=edge.command_port, state_bind=("127.0.0.1", _free_port())
    )
    assert tx._addr is None
    tx.open()
    try:
        assert tx._addr == ("127.0.0.1", edge.command_port), "sendto() must get an IP, not a name"
    finally:
        tx.close()


def test_an_unresolvable_host_fails_at_connect():
    from menlo.asimov.transport.udp import UdpTransport

    tx = UdpTransport("no-such-robot.invalid", command_port=8850, state_bind=("127.0.0.1", 0))
    with pytest.raises(ConnectError):
        tx.open()


# ── reopen hygiene and outcome naming ─────────────────────────────────────────


def test_reopen_waits_for_fresh_state_instead_of_the_last_session(edge, robot):
    robot.close()
    edge.pushing = False
    with pytest.raises(ConnectError):
        robot.open(timeout=0.4)  # the old session's sample must not satisfy this wait
    edge.pushing = True
    robot.open(timeout=2.0)
    assert robot.connected and robot.get_state().age_s < 1.0
    robot.close()


def test_a_refusal_names_the_verb_that_was_refused(edge, robot):
    from menlo.asimov import CommandRefusedError

    sent = robot.stand(wait=False)
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    with pytest.raises(CommandRefusedError) as info:
        sent.require()
    assert str(info.value).startswith("stand refused: FAULT_DAMPED")
    assert next(robot.outcomes()).name == "stand"
    assert robot.damp().wait_outcome(0.01).name == "damp"


def test_a_resolved_outcome_leaves_the_pending_table(edge, robot):
    sent = robot.stand(wait=False)
    assert sent.sequence in robot._pending
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    assert sent.sequence not in robot._pending and sent.outcome is not None


def test_state_source_allowlist_drops_everyone_else(edge):
    kw = dict(command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port))
    with pytest.raises(ConnectError):
        connect_udp("127.0.0.1", timeout=0.5, state_source="10.255.255.1", **kw)
    with connect_udp("127.0.0.1", timeout=2.0, state_source="localhost", **kw) as r:
        assert r.connected


def test_an_older_reordered_state_sample_never_overwrites_a_newer_one(edge, robot):
    from menlo.asimov.transport.udp import state_from_robot_state

    edge.pushing = False
    time.sleep(0.05)

    def sample(seq: int, mode: int):
        m = edge.state.__class__()
        m.CopyFrom(edge.state)
        m.sequence, m.current_mode = seq, mode
        return state_from_robot_state(m, None)

    high = 2**32 - 10
    robot._forget_state()  # start the stream near the wrap; from seq 1 a jump there IS "older"
    robot._on_state(sample(high, 1))  # STAND, newest
    robot._on_state(sample(high - 7, 0))  # an older DAMP sample arriving late
    assert robot.get_state().sequence == high and robot.get_state().mode is Mode.STAND
    robot._on_state(sample(0, 2))  # the counter wrapped to exactly 0: NEWER, not older
    assert robot.get_state().sequence == 0 and robot.get_state().mode is Mode.MOVE
    robot._on_state(sample(high, 0))  # and a pre-wrap straggler after that is dropped
    assert robot.get_state().sequence == 0 and robot.get_state().mode is Mode.MOVE


def test_a_restarted_firmware_counter_is_followed_not_dropped(edge, robot):
    """The firmware rebooted under a live link: its sequence counter restarts at 0. Dropping
    every sample until it climbed past the old value froze robot.get_state() for minutes."""
    from menlo.asimov.transport.udp import state_from_robot_state

    edge.pushing = False
    time.sleep(0.05)

    def sample(seq: int, mode: int, *, clock_us: int = 0):
        m = edge.state.__class__()
        m.CopyFrom(edge.state)
        m.sequence, m.current_mode, m.timestamp_us = seq, mode, clock_us
        return state_from_robot_state(m, None)

    robot._on_state(sample(5000, 0))
    robot._on_state(sample(5001, 0))
    assert robot.get_state().sequence == 5001 and robot.get_state().mode is Mode.DAMP
    robot._on_state(sample(0, 2))  # the counter restarted: a NEW stream, not an old datagram
    assert robot.get_state().sequence == 0 and robot.get_state().mode is Mode.MOVE
    robot._on_state(sample(1, 2))
    assert robot.get_state().sequence == 1, "and it keeps advancing from there"
    robot._on_state(sample(0, 0))  # a genuinely reordered straggler of the new stream: dropped
    assert robot.get_state().sequence == 1 and robot.get_state().mode is Mode.MOVE
    # a small backwards step is still the UDP reorder case, and still dropped
    robot._on_state(sample(900, 0))
    robot._on_state(sample(400, 1))
    assert robot.get_state().sequence == 900
    # inside the window, a firmware clock a second in the past is a restart too
    robot._on_state(sample(950, 0, clock_us=20_000_000))
    robot._on_state(sample(300, 2, clock_us=1_000_000))
    assert robot.get_state().sequence == 300 and robot.get_state().mode is Mode.MOVE


def test_a_wait_sees_a_restarted_stream_as_fresh_not_stale(edge, robot):
    """Staleness is the age of the last ACCEPTED sample, so following the restarted
    counter is what keeps a wait alive across a firmware reboot."""
    from menlo.asimov.transport.udp import state_from_robot_state

    edge.pushing = False
    time.sleep(0.05)
    m = edge.state.__class__()
    m.CopyFrom(edge.state)
    m.sequence = 7000
    robot._on_state(state_from_robot_state(m, None))

    def restart_arrives() -> None:
        m.sequence, m.current_mode = 3, 2
        robot._on_state(state_from_robot_state(m, None))

    threading.Timer(0.15, restart_arrives).start()  # inside stale_after: a reboot, not silence
    s = robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=2.0, stale_after=0.3)
    assert s.sequence == 3 and s.age_s < 0.3, "the restarted stream's sample is the fresh one"
    with pytest.raises(StateStaleError):  # and once nothing follows, it is stale again
        robot.wait_until(lambda s: False, timeout=2.0, stale_after=0.3)


def test_reopen_after_link_lost_is_a_working_reconnect(edge, robot):
    put_in(robot, edge, "move")
    robot.link_timeout = 0.3
    robot.set_velocity(vx=0.1, wait=False)
    edge.pushing = False
    time.sleep(0.8)
    assert not robot.connected
    robot.close()
    edge.pushing = True
    robot.open(timeout=2.0)
    assert robot.connected, "the old LinkLostError must not outlive the session that produced it"
    robot.set_velocity(vx=0.1, wait=False)  # would raise the stale LinkLostError before the fix
    assert edge.wait_for(lambda r: sum(c.HasField("policy") for c in r) >= 3)
    robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=1.0)  # waits work too
    robot.close()


def test_a_refusal_from_the_previous_session_does_not_haunt_a_reopen(edge, robot):
    from menlo.asimov import CommandRefusedError

    sent = robot.stand(wait=False)
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    with pytest.raises(CommandRefusedError):
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=1.0)
    robot.close()
    robot.open(timeout=2.0)
    # raised before the fix
    assert robot.wait_until(lambda s: s.mode is Mode.DAMP, timeout=2.0).mode is Mode.DAMP
    assert list(robot.outcomes()) == [], "old-session refusals must not surface in the new one"
    robot.close()


def test_reopen_learns_the_robot_again_instead_of_filtering_it_as_foreign(edge, robot):
    assert robot.info.dof == 25
    robot.close()
    del edge.state.joint_pos[:]
    edge.state.joint_pos.extend([0.0] * 12)  # a different unit (or firmware) on the same address
    robot.open(
        timeout=2.0
    )  # would time out before the fix: every sample dropped as "not this robot"
    assert robot.info.dof == 12 and len(robot.get_state().joints) == 12
    robot.close()


# ── limits, closed robots, verb names, foreign outcomes ────────────────────────────


@pytest.mark.parametrize("bad", [-0.6, float("nan"), float("inf"), -0.0001])
def test_limits_must_be_finite_magnitudes(bad):
    from menlo.asimov import Limits

    with pytest.raises(ValueError):
        Limits(vx=bad)


def test_a_stop_can_never_become_motion_through_the_clamp():
    from menlo.asimov import Limits

    for limits in (Limits(), Limits(vx=0.0), Limits(vx=0.1, vy=0.0, vyaw=0.0)):
        assert Velocity().clamped(limits).is_zero


def test_waits_refuse_a_closed_robot(edge, robot):
    robot.wait_until(lambda s: s.mode is Mode.DAMP, timeout=1.0)  # works while open
    robot.close()
    with pytest.raises(NotConnectedError):
        # the cached DAMP sample must not satisfy it
        robot.wait_until(lambda s: s.mode is Mode.DAMP, timeout=1.0)


def test_balance_is_named_balance(edge, robot):
    put_in(robot, edge, "move")
    sent = robot.balance()
    assert sent.name == "balance" and sent.command.is_zero
    assert robot.set_velocity(vx=0.1, wait=False).name == "set_velocity"


def test_an_outcome_for_a_sequence_we_never_sent_is_dropped(edge, robot):
    seen = []
    robot.on_refused = seen.append
    robot._tx.deliver_outcome(Refused(999_999, Refusal.FAULT_DAMPED))
    assert seen == [] and list(robot.outcomes()) == []


def test_connect_direct_takes_link_timeout(edge):
    with connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=2.0,
        link_timeout=0.7,
    ) as r:
        assert r.link_timeout == 0.7


def test_outcomes_drains_at_call_time_not_first_iteration(edge, robot):
    sent = robot.stand(wait=False)
    robot._tx.deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    it = robot.outcomes()  # not iterated
    assert len(robot._refusals) == 0, "the drain must happen when outcomes() is called"
    assert [o.name for o in it] == ["stand"]


def test_transport_open_twice_fails_loudly_instead_of_leaking(edge):
    from menlo.asimov.transport.udp import UdpTransport

    tx = UdpTransport("127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", 0))
    tx.open()
    try:
        first = tx._sock
        with pytest.raises(ConnectError):
            tx.open()  # an ephemeral bind would silently succeed and orphan the first socket
        assert tx._sock is first
    finally:
        tx.close()


def test_an_unresolvable_state_source_is_named_in_the_error(edge):
    from menlo.asimov.transport.udp import UdpTransport

    tx = UdpTransport(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", 0),
        state_source="no-such-source.invalid",
    )
    with pytest.raises(ConnectError, match="no-such-source\\.invalid"):
        tx.open()


# ── input validation and wait semantics ───────────────────────────────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
def test_duration_must_be_a_positive_finite_number(edge, robot, bad):
    with pytest.raises(ValueError):
        robot.set_velocity(vx=0.1, duration=bad, wait=False)
    assert robot._latched is None, "a rejected duration must not leave a velocity held"


@pytest.mark.parametrize(
    "kw", [{"timeout": float("nan")}, {"poll": float("inf")}, {"stale_after": 0.0}]
)
def test_wait_timing_arguments_must_be_finite(edge, robot, kw):
    with pytest.raises(ValueError):
        robot.wait_until(lambda s: True, **kw)


def test_link_timeout_must_be_a_positive_finite_number(edge):
    from menlo.asimov.transport.udp import UdpTransport

    with pytest.raises(ValueError):
        Robot(UdpTransport("127.0.0.1"), link_timeout=float("nan"))


def test_a_fault_damp_is_reported_even_when_the_wait_asked_for_damp(edge, robot):
    """`damp()` on a robot that fault-DAMPed must not read as success: the
    fault takes precedence over the predicate, and the state rides on the exception."""
    a = edge.state.active_alerts.add()
    a.id, a.severity = 7, 0  # FALL_DETECTED, critical
    edge.set_mode("damp")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not robot.get_state().faulted:
        time.sleep(0.01)  # let a sample carrying the alert arrive
    assert robot.get_state().faulted
    with pytest.raises(RobotFaultedError) as info:
        robot.wait_until(lambda s: s.mode is Mode.DAMP, timeout=2.0)
    assert info.value.state.mode is Mode.DAMP and info.value.state.alerts[0].name == "FALL_DETECTED"


def test_send_before_open_is_not_connected_not_link_lost():
    from menlo.asimov.transport.udp import UdpTransport

    with pytest.raises(NotConnectedError):
        UdpTransport("127.0.0.1").send(Velocity())


def test_a_velocity_attempted_during_a_failed_open_never_reaches_the_next_session(edge):
    """Verbs are refused until the handshake passes, and any drive latched in a previous
    attempt is cleared, so a reconnect cannot walk the robot off by itself."""
    import threading

    from menlo.asimov.transport.udp import UdpTransport

    edge.pushing = False
    robot = Robot(
        UdpTransport(
            "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
        )
    )
    stop = threading.Event()
    refused = []

    def controller() -> None:
        while not stop.is_set():
            try:
                robot.set_velocity(vx=0.25, wait=False)
            except NotConnectedError:
                refused.append(1)
            time.sleep(0.02)

    threading.Thread(target=controller, daemon=True).start()
    with pytest.raises(ConnectError):
        robot.open(timeout=0.4)
    stop.set()
    time.sleep(0.1)
    assert refused, "verbs during the handshake must raise NotConnectedError"
    edge.pushing = True
    n0 = len(edge.velocities())
    robot.open(timeout=2.0)
    time.sleep(0.4)
    assert edge.velocities()[n0:] == [], "nobody asked this session to move"
    robot.close()


def test_trajectory_needs_both_gains_or_neither(edge, robot):
    put_in(robot, edge, "move")
    with pytest.raises(ValueError):
        robot.trajectory([0.0] * 25, kp=[10.0] * 25)
    with pytest.raises(ValueError):
        robot.trajectory([0.0] * 25, kd=[1.0] * 25)
    sent = robot.trajectory([0.0] * 25, kp=[10.0] * 25, kd=[1.0] * 25)
    assert edge.wait_for(lambda r: any(c.HasField("all_trajectory") for c in r))
    c = next(c for c in edge.received if c.HasField("all_trajectory"))
    assert c.mode == 2, "a trajectory datagram says MOVE, never DAMP"
    assert sent.name == "trajectory"


def test_close_from_the_reader_thread_still_closes_the_transport(edge, robot):
    """A state callback that closes the robot runs on the transport's reader thread; the
    reader must not try to join itself, and the socket must still be released."""
    done = threading.Event()

    def on_state(_s):
        if not done.is_set():
            done.set()
            robot.close()

    robot._tx.subscribe_state(on_state)
    assert done.wait(2.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and robot._tx._sock is not None:
        time.sleep(0.02)
    assert robot._tx._sock is None and not robot.connected


def test_wait_until_keeps_a_stale_stream_as_StateStaleError(edge, robot):
    edge.pushing = False
    time.sleep(0.4)
    with pytest.raises(StateStaleError):
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=2.0, stale_after=0.3)
