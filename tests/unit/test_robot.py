"""`Robot` against a fake edge that speaks the real wire.

Every test here is a promise a script author relies on. They run without a robot; the
wire is bare protobuf over loopback UDP, so a fake edge in a thread exercises the whole
path — encoding, the hold, the waits, the error model.
"""

from __future__ import annotations

import time

import pytest

from asimov_sdk import (
    AsimovError,
    ConnectFailed,
    LinkLost,
    Mode,
    NotConnected,
    ProtocolMismatch,
    Refusal,
    Refused,
    Robot,
    RobotFaulted,
    StateStale,
    Unknown,
    Velocity,
    WaitTimedOut,
)
from asimov_sdk.robot import KEEPALIVE_HZ

# ── connect ───────────────────────────────────────────────────────────────────


def test_connect_waits_for_the_first_state_and_describes_the_robot(edge, robot):
    assert robot.connected
    assert robot.info.dof == 25
    assert robot.info.transport == "direct"
    assert robot.info.joint_names is not None and robot.info.joint_names[3] == "L_Knee"
    assert robot.state.mode is Mode.DAMP
    assert robot.state.upright is True


def test_connect_refuses_when_nobody_answers():
    """A UDP socket that hears nothing is talking to nobody. Say so, and say the fix."""
    with pytest.raises(ConnectFailed) as exc:
        Robot.connect_direct("127.0.0.1", command_port=1, state_bind=("127.0.0.1", 0), timeout=0.3)
    assert "--udp-control" in str(exc.value)


def test_connect_refuses_a_protocol_version_it_was_not_built_for(edge):
    edge.state.protocol_version = 99
    with pytest.raises(ProtocolMismatch) as exc:
        Robot.connect_direct(
            "127.0.0.1",
            command_port=edge.command_port,
            state_bind=("127.0.0.1", edge.state_port),
            timeout=2,
        )
    assert exc.value.observed == 99 and exc.value.expected == 1
    r = Robot.connect_direct(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=2,
        allow_version_skew=True,
    )
    r.close()


# ── verbs ─────────────────────────────────────────────────────────────────────


def test_stand_and_damp_are_sent_once_and_encoded_as_modes(edge, robot):
    robot.stand()
    robot.damp()
    assert edge.wait_for(lambda r: len(r) >= 2)
    time.sleep(3 / KEEPALIVE_HZ)
    assert edge.modes() == ["stand", "damp"], "a posture is an event, never re-sent"


def test_set_velocity_is_held_at_the_keepalive_rate(edge, robot):
    """The hold is what keeps the edge's 2 s watchdog from zero-STANDing the robot while
    the script is off doing vision."""
    robot.set_velocity(vx=0.2, vyaw=0.1)
    time.sleep(4 / KEEPALIVE_HZ)
    vels = edge.velocities()
    assert len(vels) >= 3, vels
    assert all(v == (0.2, 0.0, 0.1) for v in vels), "a repeat must not change the command"
    # Every velocity datagram is mode=MOVE + policy, the shape the edge's own connectors send.
    c = next(c for c in edge.received if c.HasField("policy"))
    assert c.mode == 2 and c.protocol_version == 1 and c.sequence >= 1 and c.timestamp_us > 0


def test_velocity_is_clamped_and_the_clamp_is_visible(edge, robot):
    sent = robot.set_velocity(vx=20.0, vyaw=-99.0)
    assert sent.clamped is True
    assert sent.command == Velocity(0.6, 0.0, -1.5)
    assert edge.wait_for(lambda r: any(c.HasField("policy") for c in r))
    assert edge.velocities()[-1] == (0.6, 0.0, -1.5)
    assert robot.set_velocity(vx=0.1).clamped is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_velocity_is_a_caller_bug_not_a_command(edge, robot, bad):
    """NaN sails through min/max. It has to be refused before it exists as a command."""
    with pytest.raises(ValueError):
        robot.set_velocity(vx=bad)
    time.sleep(0.05)
    assert edge.velocities() == []


def test_a_bounded_hold_ends_with_a_zero(edge, robot):
    robot.set_velocity(vx=0.3, duration=0.25)
    time.sleep(0.6)
    vels = edge.velocities()
    assert (0.3, 0.0, 0.0) in vels
    assert vels[-1] == (0.0, 0.0, 0.0), "the hold must end with an explicit zero, not silence"
    # ... and stay ended: no more datagrams after the zero.
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n


def test_stop_and_a_mode_command_end_the_hold(edge, robot):
    robot.set_velocity(vx=0.2)
    robot.stop()
    assert edge.wait_for(lambda r: len(edge.velocities()) >= 2)  # 0.2 then the zero
    n_after_stop = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n_after_stop, "zero is not re-sent"
    robot.set_velocity(vx=0.2)
    robot.stand()
    assert edge.wait_for(lambda r: len(edge.modes()) >= 1)
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert edge.velocities()[-1] == (0.2, 0.0, 0.0) and len(edge.received) == n


def test_a_superseded_velocity_never_reaches_the_robot(edge, robot):
    """The classic race: the keepalive read v1, the caller sent v2, the keepalive then
    transmitted the stale v1. Generation counting closes it."""
    robot.set_velocity(vx=0.2)
    stale_gen = robot._generation
    robot.stop()
    assert edge.wait_for(lambda r: len(edge.velocities()) >= 2)  # both real sends landed
    before = len(edge.received)
    late = robot._send("set_velocity", Velocity(0.2), stale_gen)  # the keepalive's late send
    assert late.sequence == -1, "a superseded send is reported as such, not transmitted"
    time.sleep(0.05)
    assert len(edge.received) == before


def test_trajectory_length_must_match_the_robot(edge, robot):
    with pytest.raises(ValueError):
        robot.trajectory([0.0] * 24)
    robot.trajectory([0.1] * 25, kp=[50.0] * 25)
    assert edge.wait_for(lambda r: any(c.HasField("all_trajectory") for c in r))
    c = next(c for c in edge.received if c.HasField("all_trajectory"))
    assert len(c.all_trajectory.positions) == 25 and len(c.all_trajectory.kp) == 25


# ── outcomes ──────────────────────────────────────────────────────────────────


def test_outcomes_are_unknown_until_the_edge_reports_them(edge, robot):
    sent = robot.stand()
    assert sent.outcome is None
    o = sent.wait_outcome(timeout=0.05)
    assert isinstance(o, Unknown) and o.sequence == sent.sequence
    assert sent.require(timeout=0.05) == o  # unknown is not a failure by default
    from asimov_sdk import OutcomeUnknownError

    with pytest.raises(OutcomeUnknownError):
        sent.require(timeout=0.05, unknown_ok=False)


def test_a_delivered_refusal_resolves_the_handle_and_the_callback(edge, robot):
    seen = []
    robot.on_refused = seen.append
    sent = robot.set_velocity(vx=0.2)
    robot._tx._deliver_outcome(Refused(sent.sequence, Refusal.FW_DAMPED, "firmware is DAMPed"))
    assert isinstance(sent.outcome, Refused) and sent.outcome.reason is Refusal.FW_DAMPED
    assert sent.outcome.reason.retryable is False
    assert seen and seen[0].sequence == sent.sequence
    assert [r.sequence for r in robot.outcomes()] == [sent.sequence]
    from asimov_sdk import CommandRefusedError

    with pytest.raises(CommandRefusedError):
        sent.require()


def test_an_unrecognised_refusal_is_still_a_refusal():
    assert Refusal.from_wire(4242) is Refusal.UNRECOGNIZED
    assert Refusal.from_wire(1) is Refusal.FW_DAMPED


# ── waits ─────────────────────────────────────────────────────────────────────


def test_wait_for_follows_the_robots_own_report(edge, robot):
    robot.stand()

    def flip():
        time.sleep(0.2)
        edge.set_mode("stand")

    import threading

    threading.Thread(target=flip, daemon=True).start()
    s = robot.wait_for(Mode.STAND, timeout=2.0)
    assert s.mode is Mode.STAND


def test_wait_times_out_with_a_typed_error_that_is_also_a_TimeoutError(edge, robot):
    with pytest.raises(WaitTimedOut) as exc:
        robot.wait_for(Mode.STAND, timeout=0.2)
    assert isinstance(exc.value, TimeoutError) and isinstance(exc.value, AsimovError)
    assert "STAND" in str(exc.value) and exc.value.last is not None


def test_wait_refuses_to_succeed_on_a_state_that_stopped_updating(edge, robot):
    """THE regression this design exists for: a frozen snapshot must never satisfy a wait."""
    edge.pushing = False
    time.sleep(0.4)
    with pytest.raises(StateStale) as exc:
        robot.wait_until(lambda s: True, timeout=1.0, stale_after=0.3)
    assert exc.value.last is not None and exc.value.last.age_s > 0.3


def test_wait_gives_up_early_on_a_fault_damp(edge, robot):
    a = edge.state.active_alerts.add()
    a.id, a.severity = 7, 0  # severity 0 == CRITICAL on the firmware's scale
    time.sleep(0.05)
    with pytest.raises(RobotFaulted) as exc:
        robot.wait_for(Mode.STAND, timeout=2.0)
    assert exc.value.state.faulted is True
    assert exc.value.state.alerts[0].critical is True


def test_wait_stops_when_the_pending_mode_command_is_refused(edge, robot):
    from asimov_sdk import CommandRefusedError

    sent = robot.stand()
    robot._tx._deliver_outcome(Refused(sent.sequence, Refusal.FAULT_DAMPED))
    with pytest.raises(CommandRefusedError):
        robot.wait_for(Mode.STAND, timeout=2.0)


# ── liveness & lifecycle ──────────────────────────────────────────────────────


def test_link_lost_when_state_goes_silent(edge, robot):
    robot.link_timeout = 0.3
    lost = []
    robot.on_link_lost = lost.append
    edge.pushing = False
    time.sleep(0.8)
    assert not robot.connected and lost
    with pytest.raises(LinkLost):
        robot.set_velocity(vx=0.1)


def test_close_zeroes_a_held_velocity_then_stops_talking(edge, robot):
    robot.set_velocity(vx=0.3)
    assert edge.wait_for(lambda r: any(c.HasField("policy") for c in r))
    robot.close()
    assert edge.wait_for(lambda r: any(c.HasField("policy") and c.policy.vx == 0.0 for c in r))
    assert edge.velocities()[-1] == (0.0, 0.0, 0.0)
    assert "damp" not in edge.modes(), "close() is not an emergency stop"
    n = len(edge.received)
    time.sleep(3 / KEEPALIVE_HZ)
    assert len(edge.received) == n
    robot.close()  # idempotent
    with pytest.raises(NotConnected):
        robot.stand()


def test_close_without_a_held_velocity_sends_nothing(edge, robot):
    robot.stand()
    assert edge.wait_for(lambda r: len(r) >= 1)
    n = len(edge.received)
    robot.close()
    time.sleep(0.05)
    assert len(edge.received) == n, "a standing robot gets no gratuitous zero-velocity"


def test_with_block_closes_and_the_state_names_joints(edge):
    with Robot.connect_direct(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        timeout=2,
    ) as r:
        assert r.state.joint("L_Knee").pos == 0.0
        with pytest.raises(KeyError):
            r.state.joint("left_knee")
        assert r.info.joint_index("Waist_Yaw") == 22
    assert not r.connected
