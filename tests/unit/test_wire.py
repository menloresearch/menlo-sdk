"""The wire-level contract: what the SDK encodes and how it decodes, with no sockets."""

from __future__ import annotations

import pytest

from asimov_sdk import Alert, Mode, Refusal, State, Velocity
from asimov_sdk._command import Limits, ModeCommand, Trajectory
from asimov_sdk.robots import ASIMOV_1_BIPED_JOINTS, PROTOCOL_VERSION, joint_names_for
from asimov_sdk.transport.udp import state_from_robot_state


def _robot_state(**kw):
    from asimov_protocol.v1 import asimov_state_pb2 as st_pb

    msg = st_pb.RobotState(**kw)
    return msg


def test_state_from_robot_state_is_faithful_and_typed():
    msg = _robot_state(
        current_mode=1, error_flags=0, sequence=9, timestamp_us=123, protocol_version=1
    )
    msg.joint_pos.extend([0.1] * 25)
    msg.joint_vel.extend([0.2] * 25)
    msg.joint_temp.extend([30.0] * 25)
    msg.projected_gravity.extend([0.0, 0.0, -0.99])
    msg.base_ang_vel.extend([0.0, 0.1, 0.0])
    msg.base_quat.extend([1.0, 0.0, 0.0, 0.0])
    a = msg.active_alerts.add()
    a.id, a.severity, a.value, a.threshold, a.source_id = 3, 1, 7, 5, 2

    s = state_from_robot_state(msg, joint_names_for(25))
    assert s.mode is Mode.STAND and s.sequence == 9 and s.fw_timestamp_us == 123
    assert len(s.joints) == 25 and s.joints[3].name == "L_Knee"
    assert s.joints[0].pos == pytest.approx(0.1) and s.joints[0].temp == pytest.approx(30.0)
    assert s.joints[0].current is None, "the wire did not carry current; say None, not 0"
    assert s.gravity == pytest.approx((0.0, 0.0, -0.99)) and s.upright is True
    assert s.gyro == pytest.approx((0.0, 0.1, 0.0)) and s.quat == (1.0, 0.0, 0.0, 0.0)
    assert s.alerts == (Alert(3, 1, 7.0, 5.0, 2),) and s.faulted is False
    assert s.protocol_version == PROTOCOL_VERSION
    assert s.joint("R_Knee").pos == pytest.approx(0.1)


def test_an_unknown_dof_gets_no_names_rather_than_wrong_ones():
    msg = _robot_state(current_mode=0)
    msg.joint_pos.extend([0.0] * 6)
    s = state_from_robot_state(msg, joint_names_for(6))
    assert all(j.name == "" for j in s.joints)
    with pytest.raises(KeyError):
        s.joint("L_Knee")
    assert s.gravity is None and s.upright is None


def test_faulted_reads_critical_alerts_because_error_flags_are_never_written():
    msg = _robot_state(current_mode=0)
    a = msg.active_alerts.add()
    a.severity = 0  # CRITICAL
    assert state_from_robot_state(msg, None).faulted is True
    a.severity = 2  # info
    assert state_from_robot_state(msg, None).faulted is False


def test_unknown_control_mode_is_UNKNOWN_not_a_crash():
    assert Mode.from_wire(7) is Mode.UNKNOWN


def test_velocity_clamps_symmetrically_and_reports_zero():
    assert Velocity(2, -2, 9).clamped(Limits()) == Velocity(0.6, -0.6, 1.5)
    assert Velocity().is_zero and not Velocity(vx=0.1).is_zero
    with pytest.raises(ValueError):
        Velocity(vx=True)  # a bool is not a speed


def test_mode_and_trajectory_validate_their_inputs():
    with pytest.raises(ValueError):
        ModeCommand("move")  # not a posture you can ask for
    with pytest.raises(ValueError):
        Trajectory(())
    with pytest.raises(ValueError):
        Trajectory((0.0, 0.0), kp=(1.0,))
    with pytest.raises(ValueError):
        Trajectory((float("nan"),))


def test_the_biped_joint_table_matches_the_firmware_profile_shape():
    assert len(ASIMOV_1_BIPED_JOINTS) == 25
    assert ASIMOV_1_BIPED_JOINTS[0] == "L_Hip_Pitch" and ASIMOV_1_BIPED_JOINTS[-1] == "Neck_Pitch"
    assert len(set(ASIMOV_1_BIPED_JOINTS)) == 25


def test_refusal_mirrors_the_schema_values():
    assert Refusal.FW_DAMPED == 1 and Refusal.NO_CAMERA == 12
    assert Refusal.SHUTTING_DOWN.retryable and not Refusal.GATE.retryable


def test_state_is_immutable():
    s = State(Mode.DAMP, (), None, None, None, 0, (), 0, 0, 1)
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        s.mode = Mode.STAND  # type: ignore[misc]


def test_a_missing_joint_velocity_is_None_not_zero():
    msg = _robot_state(protocol_version=1)
    msg.joint_pos.extend([0.1] * 25)  # no joint_vel on the wire
    s = state_from_robot_state(msg, joint_names_for(25))
    assert s.joints[0].vel is None, "an unreported velocity must not read as 'stationary'"
