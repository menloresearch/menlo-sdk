"""Readiness: robot.preflight(action), robot.armed, and the check each motion command runs
before it sends.

The fake edge runs with ``firmware=True`` here, so its robot mode follows the commands the
way the firmware does, including the MOVE gate: a velocity is held in STAND until STAND
has been upright for 0.5 s.
"""

from __future__ import annotations

import threading
import time

import pytest

from menlo.asimov import (
    Alert,
    Battery,
    BatteryProtection,
    ConnectionConfig,
    Joint,
    Mode,
    NotConnectedError,
    NotReadyError,
    Preflight,
    Problem,
    Robot,
    RobotFaultedError,
    State,
    WaitTimeoutError,
)
from menlo.asimov._preflight import evaluate
from tests.conftest import FakeEdge, connect_udp, put_in


def _state(
    mode: Mode = Mode.STAND,
    *,
    gravity: tuple[float, float, float] | None = (0.0, 0.0, -1.0),
    temp: float | None = 35.0,
    battery: Battery | None = None,
    error_flags: int = 0,
    alerts: tuple[Alert, ...] = (),
    received_at: float | None = None,
) -> State:
    joints = tuple(Joint(f"J{i}", 0.0, 0.0, 0.0, temp) for i in range(3))
    return State(
        mode,
        joints,
        gravity,
        (0.0, 0.0, 0.0),
        None,
        error_flags,
        alerts,
        1,
        1,
        1,
        battery=battery,
        received_at=time.monotonic() if received_at is None else received_at,
    )


def _codes(check: Preflight) -> set[str]:
    return {p.code for p in check.problems}


GOOD_BATTERY = Battery(48.0, -1.0, 80.0, 30.0, BatteryProtection(0))
LOW_BATTERY = Battery(44.0, -1.0, 12.0, 30.0, BatteryProtection(0))
PROTECTING = Battery(44.0, 0.0, 50.0, 30.0, BatteryProtection(2))


# ── the check, on one sample ─────────────────────────────────────────────────


def test_an_armed_healthy_robot_is_ready_to_move():
    check = evaluate("move", _state(battery=GOOD_BATTERY), armed=True)
    assert check.ok and check.problems == () and str(check) == "ready to move"


def test_each_action_reads_as_a_sentence():
    assert str(evaluate("stand", _state(Mode.DAMP, battery=GOOD_BATTERY), armed=False)) == (
        "ready to stand"
    )
    check = evaluate("trajectory", _state(Mode.DAMP, battery=GOOD_BATTERY), armed=False)
    assert str(check).startswith("not ready to run a trajectory:\n  - wrong_mode: ")


def test_unknown_fields_are_warnings_never_guesses():
    check = evaluate("move", _state(temp=None), armed=True)
    assert check.ok
    assert _codes(check) == {"unknown_battery", "unknown_joint_temp"}
    assert all(not p.blocking for p in check.problems)
    assert "(warning)" in str(check)


@pytest.mark.parametrize(
    ("state", "armed", "code"),
    [
        (_state(Mode.DAMP), False, "wrong_mode"),
        (_state(Mode.UNKNOWN), None, "wrong_mode"),
        (_state(Mode.STAND), False, "not_armed"),
        (_state(Mode.STAND, gravity=(0.0, 0.6, -0.8)), False, "not_armed"),
        (_state(Mode.DAMP, error_flags=1 | 1 << 8), False, "faulted"),
        (_state(battery=LOW_BATTERY), True, "battery_low"),
        (_state(battery=PROTECTING), True, "battery_protecting"),
        (_state(temp=61.0), True, "joint_hot"),
        (_state(received_at=time.monotonic() - 3.0), True, "stale_state"),
    ],
)  # fmt: skip
def test_each_blocking_problem_has_its_code(state, armed, code):
    check = evaluate("move", state, armed=armed)
    assert not check.ok and code in {p.code for p in check.blocking}, str(check)


def test_a_fault_names_the_alerts_from_error_flags():
    check = evaluate("move", _state(Mode.DAMP, error_flags=1 | (1 << 8) | (1 << 3)), armed=False)
    fault = next(p for p in check.problems if p.code == "faulted")
    assert "FALL_DETECTED" in fault.message and "MOTOR_OVERTEMP" in fault.message
    assert "until the firmware restarts" in fault.message
    # Not also "stand() it first": a latched fault is not cleared by standing.
    assert "wrong_mode" not in _codes(check)


def test_a_tilted_stand_says_how_far():
    check = evaluate("move", _state(gravity=(0.0, 0.707, -0.707)), armed=False)
    assert "tilted 45 deg" in next(p.message for p in check.problems if p.code == "not_armed")


def test_stand_is_refused_from_move_and_allowed_from_damp_and_stand():
    assert "wrong_mode" in _codes(evaluate("stand", _state(Mode.MOVE), armed=True))
    assert evaluate("stand", _state(Mode.DAMP), armed=False).ok
    assert evaluate("stand", _state(Mode.STAND), armed=False).ok


def test_trajectory_needs_what_move_needs():
    assert "wrong_mode" in _codes(evaluate("trajectory", _state(Mode.DAMP), armed=False))
    assert evaluate("trajectory", _state(Mode.MOVE), armed=True).ok


def test_no_gravity_in_stand_is_a_warning_not_a_guess():
    check = evaluate("move", _state(gravity=None), armed=None)
    assert check.ok and "unknown_gravity" in _codes(check)


def test_an_unknown_action_is_a_caller_bug():
    with pytest.raises(ValueError, match="unknown action"):
        evaluate("walk", _state(), armed=True)  # type: ignore[arg-type]


def test_problem_reads_well():
    assert str(Problem("battery_low", "battery at 12 %", True)) == "battery_low: battery at 12 %"


# ── against the fake firmware ────────────────────────────────────────────────


@pytest.fixture
def fw_edge():
    e = FakeEdge(firmware=True, state_hz=200.0)
    yield e
    e.close()


def _robot(edge: FakeEdge):
    return connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=3.0,
    )


def _no_motion(edge: FakeEdge) -> bool:
    """Nothing that would move the robot reached it: no STAND, velocity or trajectory."""
    return not any(
        c.HasField("policy") or c.HasField("all_trajectory") or c.mode == 1 for c in edge.received
    )


def test_stand_returns_once_the_robot_is_armed(fw_edge):
    with _robot(fw_edge) as robot:
        assert robot.preflight("stand").ok and robot.armed is False
        started = time.monotonic()
        robot.stand()
        assert time.monotonic() - started >= 0.45, "STAND is reported at once; arming is not"
        assert robot.get_state().mode is Mode.STAND and robot.armed is True
        assert fw_edge.armed, "the SDK must not call the robot armed before the firmware is"

        sent_at = time.monotonic()
        robot.balance()
        assert time.monotonic() - sent_at < 0.2, "no waiting for arming: armed already"
        assert robot.get_state().mode is Mode.MOVE, "balance() returns once MOVE is reported"
        assert fw_edge.move_entered_at is not None
        assert fw_edge.move_entered_at - sent_at < 0.2, "the zero velocity enters MOVE"
        assert fw_edge.velocities() == [(0.0, 0.0, 0.0)], "balance() is sent once"


def test_balance_right_after_a_stand_waits_until_the_robot_is_armed(fw_edge):
    """What the check exists to prevent: a zero velocity sent before the firmware arms
    leaves the robot in STAND, and balance() would wait for a MOVE that never comes."""
    with _robot(fw_edge) as robot:
        robot.stand(wait=False)  # returns before STAND is even reported
        robot.balance()
        assert fw_edge.armed_at is not None and fw_edge.first_velocity_at is not None
        assert fw_edge.first_velocity_at >= fw_edge.armed_at, "a velocity went out unarmed"
        assert robot.get_state().mode is Mode.MOVE


def test_balance_from_stand_without_wait_returns_once_sent(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        sent = robot.balance(wait=False)
        assert sent.name == "balance" and sent.command.is_zero
        robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=1.0)


def test_balance_that_never_reaches_move_times_out_naming_the_robot_mode(edge):
    robot = connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=3.0,
    )
    with robot:
        put_in(robot, edge, "stand")  # this fake robot stays in STAND whatever it is sent
        robot.wait_until(lambda s: robot.armed is True, timeout=2.0)
        with pytest.raises(WaitTimeoutError) as info:
            robot.balance(timeout=0.3)
        assert info.value.sent is not None and info.value.sent.name == "balance"
        assert "still reports STAND" in str(info.value)
        assert edge.velocities() == [(0.0, 0.0, 0.0)]


@pytest.mark.parametrize("fault", [False, True])
def test_balance_refuses_in_damp_and_sends_nothing(fw_edge, fault):
    with _robot(fw_edge) as robot:
        if fault:
            fw_edge.fault(7)
            time.sleep(0.1)
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.balance()
        assert time.monotonic() - started < 0.2, "DAMP does not clear by waiting"
        if fault:
            assert isinstance(info.value, RobotFaultedError) and "FALL_DETECTED" in str(info.value)
        else:
            assert info.value.has("wrong_mode") and "stand() it first" in str(info.value)
        time.sleep(0.1)
        assert fw_edge.received == [], "a refused balance() sends nothing"


def test_balance_in_move_is_never_checked(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        fw_edge.pushing = False  # a stale stream does not hold it back
        time.sleep(0.7)
        assert robot.preflight("move").has("stale_state")
        before = len(fw_edge.velocities())
        started = time.monotonic()
        robot.balance(timeout=5.0)
        assert time.monotonic() - started < 0.1, "balance() in MOVE waits for nothing"
        assert fw_edge.wait_for(lambda rx: len(fw_edge.velocities()) == before + 1)
        fw_edge.pushing = True


def test_balance_from_another_thread_ends_a_hold_without_duration(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        robot.set_velocity(vx=0.2, wait=False)  # held at 10 Hz until the next command
        assert fw_edge.wait_for(lambda rx: (0.2, 0.0, 0.0) in fw_edge.velocities())
        other = threading.Thread(target=robot.balance)
        other.start()
        other.join(timeout=2.0)
        ended = time.monotonic()
        assert robot._latched is None
        time.sleep(0.3)
        assert set(fw_edge.velocities_between(ended)) <= {(0.0, 0.0, 0.0)}
        assert fw_edge.velocities()[-1] == (0.0, 0.0, 0.0)
        assert robot.get_state().mode is Mode.MOVE


def test_set_velocity_in_stand_refuses_and_points_at_balance(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.set_velocity(vx=0.2, duration=1.0, timeout=5.0)
        assert time.monotonic() - started < 0.2, "STAND does not become MOVE by waiting"
        assert info.value.action == "move" and info.value.has("wrong_mode")
        assert "the robot is in STAND; balance() it first" in str(info.value)
        assert not info.value.has("not_armed")
        time.sleep(0.2)
        assert fw_edge.velocities() == [] and robot.get_state().mode is Mode.STAND


def test_set_velocity_waits_by_default_and_needs_a_duration(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        before = len(fw_edge.received)
        with pytest.raises(ValueError, match="needs a duration= to wait for, or wait=False"):
            robot.set_velocity(vx=0.2)
        time.sleep(0.1)
        assert len(fw_edge.received) == before, "a ValueError sends nothing"
        started = time.monotonic()
        robot.set_velocity(vx=0.2, duration=0.3)
        assert time.monotonic() - started >= 0.3, "the default waits for the duration"
        assert fw_edge.wait_for(lambda rx: fw_edge.velocities()[-1] == (0.0, 0.0, 0.0))


def test_set_velocity_hold_false_sends_one_packet_and_never_re_sends(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        before = len(fw_edge.velocities())
        started = time.monotonic()
        sent = robot.set_velocity(vx=0.6, hold=False)  # no duration, no wait: not a ValueError
        assert time.monotonic() - started < 0.05, "a ready robot adds no delay"
        assert sent.clamped and sent.command.vx == 0.4, "the limits still apply"
        time.sleep(1.0)
        assert fw_edge.velocities()[before:] == [(0.4, 0.0, 0.0)], "one packet, no re-sends"
        robot.set_velocity(vx=0.1, hold=False, wait=False)  # an explicit wait=False is fine
        assert fw_edge.wait_for(lambda rx: fw_edge.velocities()[-1] == (0.1, 0.0, 0.0))


def test_set_velocity_hold_false_ends_a_running_hold(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        robot.set_velocity(vx=0.3, wait=False)  # held at 10 Hz
        assert fw_edge.wait_for(lambda rx: (0.3, 0.0, 0.0) in fw_edge.velocities())
        robot.set_velocity(vx=0.1, hold=False)
        after = time.monotonic()
        time.sleep(0.5)
        assert fw_edge.velocities_between(after + 0.05) == [], "the old hold was re-sent"
        assert fw_edge.velocities()[-1] == (0.1, 0.0, 0.0)


def test_set_velocity_hold_false_refuses_at_once_when_not_ready(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.set_velocity(vx=0.1, hold=False)  # STAND: balance() first
        assert info.value.has("wrong_mode")
        robot.balance()
        fw_edge.pushing = False
        time.sleep(0.6)
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.set_velocity(vx=0.1, hold=False)  # a loop must not stall on a stale stream
        assert time.monotonic() - started < 0.1 and info.value.has("stale_state")
        fw_edge.pushing = True
        assert (0.1, 0.0, 0.0) not in fw_edge.velocities()


def test_set_velocity_hold_false_takes_no_duration_and_no_wait(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        before = len(fw_edge.received)
        with pytest.raises(ValueError, match="no duration="):
            robot.set_velocity(vx=0.1, hold=False, duration=1.0)
        with pytest.raises(ValueError, match="nothing to wait for"):
            robot.set_velocity(vx=0.1, hold=False, wait=True)
        time.sleep(0.1)
        assert len(fw_edge.received) == before


def test_close_zeroes_a_velocity_sent_with_hold_false(fw_edge):
    robot = _robot(fw_edge)
    robot.stand()
    robot.balance()
    robot.set_velocity(vx=0.2, hold=False)
    robot.close()
    assert fw_edge.wait_for(lambda rx: fw_edge.velocities()[-1] == (0.0, 0.0, 0.0))


def test_move_to_stand_stays_armed(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        fw_edge.set_mode("stand")  # an operator's STAND from MOVE
        robot.wait_until(lambda s: s.mode is Mode.STAND, timeout=1.0)
        assert robot.armed is True and robot.preflight("move").ok


def test_stand_is_refused_in_move_and_sends_nothing(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        stands = fw_edge.modes().count("stand")
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.stand()
        assert time.monotonic() - started < 0.2, "a robot mode that is wrong does not clear"
        assert info.value.action == "stand" and info.value.has("wrong_mode")
        assert "balance()" in str(info.value), "the message says what to do instead"
        time.sleep(0.1)
        assert fw_edge.modes().count("stand") == stands


@pytest.mark.parametrize("verb", ["set_velocity", "trajectory", "set_joints"])
def test_in_damp_a_motion_command_refuses_at_once_and_sends_nothing(fw_edge, verb):
    with _robot(fw_edge) as robot:
        pose = list(robot.get_state().joint_pos)
        call = {
            "set_velocity": lambda: robot.set_velocity(vx=0.2, timeout=5.0, wait=False),
            "trajectory": lambda: robot.trajectory(pose, timeout=5.0),
            "set_joints": lambda: robot.set_joints(pose, duration=0.2, wait=False),
        }[verb]
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            call()
        assert time.monotonic() - started < 0.2
        assert info.value.action == ("move" if verb == "set_velocity" else "trajectory")
        assert info.value.has("wrong_mode") and "stand() it first" in str(info.value)
        assert info.value.preflight is not None and not info.value.preflight.ok
        time.sleep(0.1)
        assert fw_edge.received == [], "a refused command sends nothing"


def test_a_tilted_robot_never_arms(fw_edge):
    fw_edge.state.projected_gravity[:] = [0.0, 0.6, -0.8]
    with _robot(fw_edge) as robot:
        started = time.monotonic()
        with pytest.raises(WaitTimeoutError) as timed_out:
            robot.stand(timeout=0.8)
        assert time.monotonic() - started >= 0.75
        assert "has not armed" in str(timed_out.value) and timed_out.value.sent is not None
        assert robot.get_state().mode is Mode.STAND

        with pytest.raises(NotReadyError) as info:
            robot.balance(timeout=0.3)
        assert info.value.has("not_armed") and "tilted" in str(info.value)
        assert "waited 0.3 s" in str(info.value)
        assert robot.armed is False and not fw_edge.armed
        assert fw_edge.velocities() == []


@pytest.mark.parametrize("verb", ["stand", "balance", "set_velocity", "trajectory", "set_joints"])
def test_a_latched_fault_refuses_every_motion_command_at_once(fw_edge, verb):
    with _robot(fw_edge) as robot:
        fw_edge.fault(7)
        robot.wait_until(lambda s: True, timeout=1.0)
        time.sleep(0.05)
        pose = list(robot.get_state().joint_pos)
        call = {
            "stand": lambda: robot.stand(timeout=5.0),
            "balance": lambda: robot.balance(timeout=5.0),
            "set_velocity": lambda: robot.set_velocity(vx=0.2, timeout=5.0, wait=False),
            "trajectory": lambda: robot.trajectory(pose, timeout=5.0),
            "set_joints": lambda: robot.set_joints(pose, duration=0.2, wait=False, timeout=5.0),
        }[verb]
        started = time.monotonic()
        with pytest.raises(RobotFaultedError) as info:
            call()
        assert time.monotonic() - started < 0.5, "a latched fault does not clear by waiting"
        assert isinstance(info.value, NotReadyError), "except NotReadyError catches a fault"
        assert info.value.has("faulted") and "FALL_DETECTED" in str(info.value)
        assert "until the firmware restarts" in str(info.value)
        assert info.value.sent is None and info.value.state.faulted
        time.sleep(0.1)
        assert _no_motion(fw_edge)


def test_a_fault_while_standing_is_raised_by_stand(fw_edge):
    with _robot(fw_edge) as robot:
        threading.Timer(0.15, fw_edge.fault, args=(7,)).start()  # before the 0.5 s arming
        with pytest.raises(RobotFaultedError) as info:
            robot.stand()
        assert info.value.sent is not None and info.value.sent.name == "stand"
        assert info.value.action == "stand" and "FALL_DETECTED" in str(info.value)


def test_damp_is_never_checked(fw_edge):
    with _robot(fw_edge) as robot:
        fw_edge.fault(7)
        time.sleep(0.1)
        assert not robot.preflight("move").ok and not robot.preflight("stand").ok
        robot.damp()  # returns: a fault-DAMPed robot is limp, which is what damp asks for
        assert fw_edge.wait_for(lambda rx: fw_edge.modes().count("damp") == 1)

        fw_edge.pushing = False  # and a stale stream does not hold it back either
        time.sleep(0.7)
        robot.damp(wait=False)
        assert fw_edge.wait_for(lambda rx: fw_edge.modes().count("damp") == 2)


def test_damp_returns_once_damp_is_reported(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.damp()
        assert robot.get_state().mode is Mode.DAMP and robot.armed is False


def test_damp_that_is_not_reported_times_out_naming_the_robot_mode(edge):
    robot = connect_udp(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=3.0,
    )
    with robot:
        put_in(robot, edge, "move")  # this fake robot ignores the DAMP
        with pytest.raises(WaitTimeoutError) as info:
            robot.damp(timeout=0.3)
        assert info.value.sent is not None and info.value.sent.name == "damp"
        assert "still reports MOVE" in str(info.value) and "E-Stop" in str(info.value)


def test_a_command_waits_out_a_stale_stream_that_recovers(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        fw_edge.pushing = False
        time.sleep(0.6)
        assert robot.preflight("move").has("stale_state")
        threading.Timer(0.3, setattr, args=(fw_edge, "pushing", True)).start()
        robot.set_velocity(vx=0.1, timeout=3.0, wait=False)
        assert fw_edge.wait_for(lambda rx: (0.1, 0.0, 0.0) in fw_edge.velocities())
        robot.balance()


def test_a_ready_trajectory_check_adds_no_delay(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        pose = list(robot.get_state().joint_pos)
        robot.trajectory(pose)
        robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=1.0)
        started = time.monotonic()
        for _ in range(50):
            robot.trajectory(pose)
        assert (time.monotonic() - started) / 50 < 0.005, "a 50 Hz loop must keep its rate"
        robot.damp()


def test_a_streaming_trajectory_raises_at_once_on_a_stale_stream(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        pose = list(robot.get_state().joint_pos)
        robot.trajectory(pose)
        robot.wait_until(lambda s: s.mode is Mode.MOVE, timeout=1.0)
        fw_edge.pushing = False
        time.sleep(0.6)
        sent_before = len(fw_edge.received)
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.trajectory(pose)  # a 50 Hz loop must not stall on a stale stream
        assert time.monotonic() - started < 0.1
        assert info.value.has("stale_state") and len(fw_edge.received) == sent_before
        fw_edge.pushing = True


def test_fault_damp_is_a_latched_fault(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.balance()
        robot.set_velocity(vx=0.2, wait=False)  # held at 10 Hz until something ends it
        fw_edge.fault_damp()
        deadline = time.monotonic() + 1.0
        while robot.get_state().mode is not Mode.FAULT_DAMP and time.monotonic() < deadline:
            time.sleep(0.01)
        s = robot.get_state()
        assert s.mode is Mode.FAULT_DAMP and s.faulted and s.error_flags == 0
        assert robot.armed is False
        time.sleep(0.15)
        assert robot._latched is None, "FAULT_DAMP releases a held velocity"
        check = robot.preflight("move")
        assert check.has("faulted") and not check.has("wrong_mode")
        assert "FAULT_DAMP" in next(p.message for p in check.problems if p.code == "faulted")
        with pytest.raises(RobotFaultedError, match="FAULT_DAMP"):
            robot.stand()
        with pytest.raises(RobotFaultedError, match="FAULT_DAMP"):
            robot.wait_until(lambda s: False, timeout=1.0)
        robot.damp()  # limp already: returns


def test_fault_damp_alone_is_reported_as_the_fault():
    check = evaluate("stand", _state(Mode.FAULT_DAMP), armed=False)
    assert not check.ok and _codes(check) >= {"faulted"} and "wrong_mode" not in _codes(check)
    assert "robot mode FAULT_DAMP" in check.problems[0].message
    assert Mode.from_wire(5) is Mode.FAULT_DAMP


def test_a_refusal_names_every_problem_and_its_fix():
    check = evaluate("move", _state(battery=LOW_BATTERY, temp=61.0), armed=True)
    text = check.explain()
    assert text.startswith("not ready to move: ")
    assert "(battery_low); charge the battery" in text
    assert "(joint_hot); let the actuators cool" in text


def test_a_closed_robot_is_not_connected(fw_edge):
    robot = _robot(fw_edge)
    robot.close()
    check = robot.preflight()
    assert not check.ok and check.has("not_connected") and robot.armed is None
    with pytest.raises(NotConnectedError):
        robot.stand()
    with pytest.raises(NotConnectedError):
        robot.set_velocity(vx=0.1, wait=False)


def test_without_gravity_stand_returns_at_stand_and_arming_is_unknown(fw_edge):
    del fw_edge.state.projected_gravity[:]
    with _robot(fw_edge) as robot:
        robot.stand(timeout=2.0)
        assert robot.get_state().mode is Mode.STAND and robot.armed is None
        assert robot.preflight("move").has("unknown_gravity")


def _tracked(samples: list[tuple[float, tuple[float, float, float] | None]]) -> bool:
    """Feed STAND samples ``(received_at, gravity)`` to the arming tracker; return armed."""
    robot = Robot(ConnectionConfig())
    prev: State | None = None
    for at, gravity in samples:
        state = _state(gravity=gravity, received_at=at)
        robot._track_arming(prev, state, False)
        prev = state
    return robot._armed


UP = (0.0, 0.0, -1.0)


def test_an_observed_upright_hold_arms():
    assert _tracked([(t / 10, UP) for t in range(7)])


def test_a_sample_without_gravity_restarts_the_hold():
    assert not _tracked([(0.0, UP), (0.1, UP), (0.2, None), (0.3, UP), (0.5, UP), (0.7, UP)])


def test_a_gap_between_samples_restarts_the_hold():
    assert not _tracked([(0.0, UP), (0.1, UP), (0.6, UP), (0.7, UP)])
