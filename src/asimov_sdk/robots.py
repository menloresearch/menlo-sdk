"""Per-robot facts the wire does not carry.

The SDK cannot ask a robot for its
joint names. Until it can, the tables here fill in what a 25-DOF ``RobotState`` means.
Every table names its source; if that source moves, the table is wrong and must move
with it — do not "fix" a name here without re-reading the firmware profile.
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

#: The ``asimov.io`` protocol version this SDK was built against. The firmware rejects
#: commands carrying any other value (``netrx_thread.c: ASIMOV_PROTOCOL_VERSION``), and
#: echoes its own in ``RobotState.protocol_version``; ``Robot.connect_*`` compares the two.
PROTOCOL_VERSION = 1


def joint_names_for(dof: int) -> tuple[str, ...] | None:
    """Best-known joint names for a body with ``dof`` motors, or ``None``.

    Only the biped is known. A 6-motor arm gets ``None`` — joints are then addressed by
    index and ``State.joint(name)`` raises — which is better than a wrong name.
    """
    if dof == len(ASIMOV_1_BIPED_JOINTS):
        return ASIMOV_1_BIPED_JOINTS
    return None
