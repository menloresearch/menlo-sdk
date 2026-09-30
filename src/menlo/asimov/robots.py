"""Per-robot facts the wire does not carry.

The SDK cannot ask a robot for its joint names, so the tables here say what a 25-DOF
``RobotState`` means. Every table names its source; if that source moves, the table is
wrong and must move with it. Do not "fix" a name here without re-reading the firmware
profile.
"""

from __future__ import annotations

#: asimov-firmware ``source/robot/asimov_1_biped.c`` motor table, in table order, at
#: commit 4925000b407 (2026-09-02). This IS the index order of ``RobotState.joint_pos``.
ASIMOV_1_BIPED_JOINTS: tuple[str, ...] = (
    "L_Hip_Pitch",
    "L_Hip_Roll",
    "L_Hip_Yaw",
    "L_Knee",
    "L_Ankle_A",
    "L_Ankle_B",
    "R_Hip_Pitch",
    "R_Hip_Roll",
    "R_Hip_Yaw",
    "R_Knee",
    "R_Ankle_A",
    "R_Ankle_B",
    "L_Shoulder_Pitch",
    "L_Shoulder_Roll",
    "L_Shoulder_Yaw",
    "L_Elbow",
    "L_Wrist_Yaw",
    "R_Shoulder_Pitch",
    "R_Shoulder_Roll",
    "R_Shoulder_Yaw",
    "R_Elbow",
    "R_Wrist_Yaw",
    "Waist_Yaw",
    "Neck_Yaw",
    "Neck_Pitch",
)

#: The biped's ankles are a parallel linkage: motors A and B together set the ankle's pitch
#: and roll. ``RobotState.joint_pos`` reports the motors, but the firmware reads a
#: trajectory's two ankle entries as the joint itself (pitch, roll) and turns them into
#: motor targets: A = K_PITCH * pitch - K_ROLL * roll, B = -K_PITCH * pitch - K_ROLL * roll,
#: with pitch limited to 0.35 rad and roll to 0.1 rad either way. asimov-firmware
#: ``source/app/policy/policy_thread.c`` (``apply_ankle_coupling``), 2026-09-28.
ANKLE_K_PITCH = 2.02
ANKLE_K_ROLL = 0.8
ANKLE_PITCH_LIMIT_RAD = 0.35
ANKLE_ROLL_LIMIT_RAD = 0.1
#: How far past an ankle limit a target may sit and still be sent: a reported pose carries
#: tracking error, so a pose the firmware holds at a limit reads a little past it.
ANKLE_LIMIT_MARGIN_RAD = 0.02
#: (A, B) index pairs of the biped's ankles in ``ASIMOV_1_BIPED_JOINTS``.
ASIMOV_1_BIPED_ANKLES: tuple[tuple[int, int], ...] = ((4, 5), (10, 11))

#: The ``asimov.io`` protocol version this SDK was built against. The firmware rejects
#: commands carrying any other value (``netrx_thread.c: ASIMOV_PROTOCOL_VERSION``), and
#: echoes its own in ``RobotState.protocol_version``; ``Robot.connect()`` compares the two.
PROTOCOL_VERSION = 1


def joint_names_for(dof: int) -> tuple[str, ...] | None:
    """Best-known joint names for a body with ``dof`` motors, or ``None``.

    Only the biped is known. A 6-motor arm gets ``None``: joints are then addressed by
    index and ``State.joint(name)`` raises, which is better than a wrong name.
    """
    if dof == len(ASIMOV_1_BIPED_JOINTS):
        return ASIMOV_1_BIPED_JOINTS
    return None


def check_ankle_limits(positions: tuple[float, ...]) -> None:
    """``ValueError`` when a biped trajectory target puts an ankle outside the pitch or roll
    the firmware limits it to (more than ``ANKLE_LIMIT_MARGIN_RAD`` past it). The firmware
    would clamp such a target, so the ankle would not go where it was sent. Positions are
    in the frame ``RobotState.joint_pos`` reports. Any other body is not checked."""
    if len(positions) != len(ASIMOV_1_BIPED_JOINTS):
        return
    for a, b in ASIMOV_1_BIPED_ANKLES:
        motor_a, motor_b = positions[a], positions[b]
        side = ASIMOV_1_BIPED_JOINTS[a].removesuffix("_A")
        pitch = (motor_a - motor_b) / (2 * ANKLE_K_PITCH)
        roll = -(motor_a + motor_b) / (2 * ANKLE_K_ROLL)
        for what, value, limit in (
            ("pitch", pitch, ANKLE_PITCH_LIMIT_RAD),
            ("roll", roll, ANKLE_ROLL_LIMIT_RAD),
        ):
            if abs(value) > limit + ANKLE_LIMIT_MARGIN_RAD:
                raise ValueError(
                    f"{side} target ({ASIMOV_1_BIPED_JOINTS[a]}={motor_a:.3f}, "
                    f"{ASIMOV_1_BIPED_JOINTS[b]}={motor_b:.3f}) is ankle {what} {value:.3f} "
                    f"rad; the firmware limits ankle {what} to {limit} rad either way"
                )


def trajectory_wire_positions(positions: tuple[float, ...]) -> tuple[float, ...]:
    """Trajectory targets in the frame ``RobotState.joint_pos`` reports -> the values the
    firmware expects on the wire. On the biped each ankle's motor targets (A, B) become the
    ankle pitch and roll the firmware reads there; the firmware turns them back into the
    same A and B, within its ankle limits. Any other body is sent as given."""
    if len(positions) != len(ASIMOV_1_BIPED_JOINTS):
        return positions
    out = list(positions)
    for a, b in ASIMOV_1_BIPED_ANKLES:
        motor_a, motor_b = out[a], out[b]
        out[a] = (motor_a - motor_b) / (2 * ANKLE_K_PITCH)  # pitch
        out[b] = -(motor_a + motor_b) / (2 * ANKLE_K_ROLL)  # roll
    return tuple(out)
