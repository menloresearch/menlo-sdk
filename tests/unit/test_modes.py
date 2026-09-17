"""The same promises, three times: udp (A0), hybrid (A) and pure LiveKit (B).

The rule this file exists to enforce: if a feature only works in one mode, it is not done.
Every test here is mode-agnostic behaviour — a verb, a hold, a wait, the error model — and
the ``any_robot`` fixture runs it over each lane against the same fake edge.

Mode-SPECIFIC behaviour lives elsewhere: the UDP lane's sockets and datagram filtering in
``test_robot.py``, the room's topics and media in ``test_livekit.py``.
"""

from __future__ import annotations

import json
import time

import pytest

from asimov_sdk import LinkLostError, Mode, NotConnectedError, StateStaleError, Unknown
from asimov_sdk.recording import load
from asimov_sdk.robot import KEEPALIVE_HZ

MODES = {"udp", "hybrid", "livekit"}


def test_every_mode_is_exercised(any_robot):
    mode, robot = any_robot
    assert mode in MODES
    assert robot.info.transport == mode


def test_connect_describes_the_same_robot_on_every_lane(any_robot):
    _mode, robot = any_robot
    assert robot.connected and robot.info.dof == 25
    assert robot.info.joint_names is not None and robot.info.joint_names[3] == "L_Knee"
    assert robot.state.mode is Mode.DAMP and robot.state.upright is True
    assert "drive" in robot.info.capabilities and "state" in robot.info.capabilities


def test_stand_and_damp_are_sent_once_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    robot.stand()
    assert edge.wait_for(lambda rx: edge.modes().count("stand") == 1)
    robot.damp()
    assert edge.wait_for(lambda rx: edge.modes().count("damp") == 1)
    time.sleep(0.3)
    assert edge.modes().count("stand") == 1, "a mode command must never be re-sent"
    assert edge.modes().count("damp") == 1


def test_a_velocity_is_held_at_the_keepalive_rate_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    robot.set_velocity(vx=0.3)
    time.sleep(0.6)
    held = [v for v in edge.velocities() if v == (0.3, 0.0, 0.0)]
    assert len(held) >= int(0.6 * KEEPALIVE_HZ * 0.5), f"only {len(held)} frames in 0.6 s"


def test_the_clamp_is_applied_and_visible_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    sent = robot.set_velocity(vx=9.0)
    assert sent.clamped and sent.command.vx == pytest.approx(0.6)
    assert edge.wait_for(lambda rx: (0.6, 0.0, 0.0) in edge.velocities())


def test_stop_ends_the_hold_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    robot.set_velocity(vx=0.2)
    assert edge.wait_for(lambda rx: (0.2, 0.0, 0.0) in edge.velocities())
    robot.stop()
    assert edge.wait_for(lambda rx: edge.velocities()[-1] == (0.0, 0.0, 0.0))
    settled = len(edge.velocities())
    time.sleep(0.3)
    later = edge.velocities()[settled:]
    assert all(v == (0.0, 0.0, 0.0) for v in later), f"the hold outlived stop(): {later}"


def test_a_bounded_hold_ends_with_a_zero_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    robot.set_velocity(vx=0.2, duration=0.2)
    assert edge.wait_for(lambda rx: edge.velocities()[-1:] == [(0.0, 0.0, 0.0)], timeout=2.0)


def test_wait_for_reads_the_robots_own_report_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    robot.stand()
    edge.set_mode("stand")
    assert robot.wait_for(Mode.STAND, timeout=3.0).mode is Mode.STAND


def test_a_quiet_stream_is_stale_not_slow_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    edge.pushing = False
    with pytest.raises(StateStaleError):
        robot.wait_until(lambda s: False, timeout=3.0, stale_after=0.3)


def test_link_loss_is_declared_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    lost: list[LinkLostError] = []
    robot.on_link_lost = lost.append
    edge.pushing = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not lost:
        time.sleep(0.05)
    assert lost, "the link went quiet and nobody said so"
    assert not robot.connected
    with pytest.raises(LinkLostError):
        robot.stand()


def test_close_zeroes_a_held_velocity_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    robot.set_velocity(vx=0.4)
    assert edge.wait_for(lambda rx: (0.4, 0.0, 0.0) in edge.velocities())
    robot.close()
    assert edge.velocities()[-1] == (0.0, 0.0, 0.0), "close() left the robot walking"
    with pytest.raises(NotConnectedError):
        robot.stand()


def test_a_trajectory_is_validated_and_encoded_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    with pytest.raises(ValueError):
        robot.trajectory([0.0] * 3)  # this robot has 25 motors
    robot.trajectory([0.1] * 25, kp=[10.0] * 25, kd=[1.0] * 25)
    assert edge.wait_for(lambda rx: any(c.HasField("all_trajectory") for c in rx))
    cmd = next(c for c in edge.received if c.HasField("all_trajectory"))
    assert len(cmd.all_trajectory.positions) == 25
    assert cmd.all_trajectory.positions[0] == pytest.approx(0.1)
    assert cmd.mode == 2, "a trajectory drives; the packet must not say DAMP"


def test_goto_interpolates_from_the_reported_pose_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    edge.state.joint_pos[:] = [0.0] * 25
    time.sleep(0.05)
    robot.goto([0.0] * 25, duration=0.2, hz=20.0, wait=True, timeout=3.0)
    assert edge.wait_for(lambda rx: any(c.HasField("all_trajectory") for c in rx))


def test_an_outcome_is_unknown_until_the_edge_reports_one_on_every_lane(any_robot):
    _mode, robot = any_robot
    outcome = robot.stand().wait_outcome(timeout=0.2)
    assert isinstance(outcome, Unknown), "no lane invents a verdict the edge did not send"
    assert outcome.name == "stand" and outcome.waited_s >= 0.2


def test_recording_captures_state_and_commands_on_every_lane(any_robot, tmp_path):
    _mode, robot = any_robot
    path = tmp_path / "run.jsonl"
    with robot.record(path):
        robot.stand()
        time.sleep(0.2)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(r["kind"] == "state" for r in rows)
    assert any(r["kind"] == "sent" and r["name"] == "stand" for r in rows)
    assert load(path)


def test_the_sequence_a_verb_reports_is_the_one_on_the_wire_on_every_lane(edge, any_robot):
    _mode, robot = any_robot
    sent = robot.stand()
    assert edge.wait_for(lambda rx: any(c.sequence == sent.sequence for c in rx))
    match = next(c for c in edge.received if c.sequence == sent.sequence)
    assert match.mode == 1 and match.protocol_version == robot.info.protocol_version
