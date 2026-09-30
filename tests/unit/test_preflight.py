"""Readiness: robot.preflight(action), robot.armed and robot.wait_ready(action).

The fake edge runs with ``firmware=True`` here, so its robot mode follows the commands the
way the firmware does, including the MOVE gate: a velocity is held in STAND until STAND
has been upright for 0.5 s.
"""

from __future__ import annotations

import time

import pytest

from menlo.asimov import (
    Alert,
    Battery,
    BatteryProtection,
    ConnectionConfig,
    Joint,
    Mode,
    NotReadyError,
    Preflight,
    Problem,
    Robot,
    State,
)
from menlo.asimov._preflight import evaluate
from tests.conftest import FakeEdge, connect_udp


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


def test_stand_is_reported_before_the_robot_is_armed_and_wait_ready_waits_for_it(fw_edge):
    with _robot(fw_edge) as robot:
        assert robot.preflight("stand").ok and robot.armed is False
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=2.0)
        stood = time.monotonic()
        assert not fw_edge.armed, "STAND is reported at once; the firmware arms 0.5 s later"
        early = robot.preflight("move")
        assert not early.ok and early.has("not_armed")

        ready = robot.wait_ready("move", timeout=3.0)
        assert ready.ok and robot.armed is True
        assert time.monotonic() - stood >= 0.45
        assert fw_edge.armed, "the SDK must not call the robot armed before the firmware is"

        sent_at = time.monotonic()
        robot.set_velocity(vx=0.2)
        robot.wait_for(Mode.MOVE, timeout=1.0)
        assert fw_edge.move_entered_at is not None
        assert fw_edge.move_entered_at - sent_at < 0.2, "the first velocity enters MOVE"
        robot.stop()


def test_a_velocity_sent_before_arming_is_held_in_stand(fw_edge):
    """What wait_ready("move") exists to prevent: the hold's clock runs while the firmware
    keeps the robot in STAND, so a short timed walk never walks."""
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=2.0)
        robot.set_velocity(vx=0.2, duration=0.3, wait=True)
        assert fw_edge.move_entered_at is None and robot.state.mode is Mode.STAND


def test_move_to_stand_stays_armed(fw_edge):
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.wait_ready("move", timeout=3.0)
        robot.set_velocity(vx=0.1)
        robot.wait_for(Mode.MOVE, timeout=1.0)
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=1.0)
        assert robot.armed is True and robot.preflight("move").ok


def test_a_tilted_robot_never_arms_and_wait_ready_times_out(fw_edge):
    fw_edge.state.projected_gravity[:] = [0.0, 0.6, -0.8]
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=2.0)
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.wait_ready("move", timeout=0.8)
        assert time.monotonic() - started >= 0.75
        assert info.value.preflight.has("not_armed") and "tilted" in str(info.value)
        assert robot.armed is False and not fw_edge.armed


def test_wait_ready_gives_up_at_once_on_a_fault(fw_edge):
    with _robot(fw_edge) as robot:
        fw_edge.fault(7)
        time.sleep(0.1)
        started = time.monotonic()
        with pytest.raises(NotReadyError) as info:
            robot.wait_ready("move", timeout=5.0)
        assert time.monotonic() - started < 1.0, "a latched fault does not clear by waiting"
        assert info.value.preflight.has("faulted") and "FALL_DETECTED" in str(info.value)
        robot.stand()
        time.sleep(0.2)
        assert robot.state.mode is Mode.DAMP, "the latch ignores STAND"


def test_wait_ready_in_damp_times_out_with_wrong_mode(fw_edge):
    with _robot(fw_edge) as robot, pytest.raises(NotReadyError) as info:
        robot.wait_ready("move", timeout=0.3)
    assert info.value.preflight.has("wrong_mode")


def test_a_closed_robot_is_not_connected(fw_edge):
    robot = _robot(fw_edge)
    robot.close()
    check = robot.preflight()
    assert not check.ok and check.has("not_connected") and robot.armed is None
    with pytest.raises(NotReadyError):
        robot.wait_ready(timeout=5.0)


def test_armed_is_unknown_without_gravity(fw_edge):
    del fw_edge.state.projected_gravity[:]
    with _robot(fw_edge) as robot:
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=2.0)
        assert robot.armed is None
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
