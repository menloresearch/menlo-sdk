"""The bytes every lane shares: ``asimov.io.RobotCommand`` out, ``asimov.io.RobotState`` in.

The UDP lane puts one bare message per datagram; the LiveKit lane puts the same bare
command in a reliable data packet on the ``commands`` topic and the same bare state in
each frame of a data track named ``state``. There is no envelope and no type tag on either
wire — the port, the topic or the track name says what the bytes are. So the encoder and
the decoder live here and neither transport owns them.
"""

from __future__ import annotations

import time
from typing import Any

from asimov_sdk import _proto, robots
from asimov_sdk._command import Command, ModeCommand, Trajectory, Velocity
from asimov_sdk._errors import ConnectError
from asimov_sdk._state import Alert, Battery, BatteryProtection, Joint, Mode, State

#: LiveKit data topic carrying one serialized ``asimov.io.RobotCommand`` per packet.
COMMAND_TOPIC = "commands"
#: Name of the robot's LiveKit data track; each frame is one serialized
#: ``asimov.io.RobotState`` — the same bytes the UDP lane echoes on :8851 — and the frame's
#: ``user_timestamp`` is the edge's clock (µs since the epoch) when the sample arrived from
#: the firmware.
STATE_TRACK = "state"


def _pb() -> tuple[Any, Any, Any]:
    """The generated bindings, imported lazily so importing the SDK never needs protobuf
    until a transport is actually opened (and so the error names the fix)."""
    try:
        b = _proto.load()
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise ConnectError(
            "protobuf is not installed; it is the SDK's only runtime dependency "
            "(`pip install protobuf>=5.29.3`)."
        ) from exc
    return b.command, b.common, b.state


def encode_command(command: Command, sequence: int) -> bytes:
    """One neutral :data:`~asimov_sdk._command.Command` -> one serialized
    ``asimov.io.RobotCommand``, stamped with ``sequence`` and the sender's wall clock.

    The same bytes go in a UDP datagram and in a LiveKit data packet: the edge parses one
    message either way, and the arbiter sees the SDK as one more connector.
    """
    cmd, common, _ = _pb()
    msg = cmd.RobotCommand(protocol_version=robots.PROTOCOL_VERSION)
    if isinstance(command, Velocity):
        # mode=MOVE + policy: the shape the edge's own BLE connector and asimov-manager
        # send. The arbiter routes on HasField("policy").
        msg.mode = common.CONTROL_MODE_MOVE
        msg.command_control = common.COMMAND_CONTROL_POLICY
        msg.policy.vx = command.vx
        msg.policy.vy = command.vy
        msg.policy.vyaw = command.vyaw
    elif isinstance(command, ModeCommand):
        msg.mode = (
            common.CONTROL_MODE_STAND if command.mode == "stand" else common.CONTROL_MODE_DAMP
        )
    elif isinstance(command, Trajectory):
        msg.mode = common.CONTROL_MODE_MOVE  # a trajectory drives; the packet must not say DAMP
        msg.command_control = common.COMMAND_CONTROL_TRAJECTORY
        msg.all_trajectory.positions.extend(command.positions)
        if command.kp is not None:
            msg.all_trajectory.kp.extend(command.kp)
        if command.kd is not None:
            msg.all_trajectory.kd.extend(command.kd)
    else:  # pragma: no cover - the Command union is closed
        raise TypeError(f"unsupported command {command!r}")
    msg.sequence = sequence & 0xFFFFFFFF
    # The edge rejects commands stamped more than 5 s in the past or 2 s in the future.
    msg.timestamp_us = int(time.time() * 1_000_000)
    return bytes(msg.SerializeToString())


def state_from_robot_state(
    msg: Any, joint_names: tuple[str, ...] | None, edge_timestamp_us: int = 0
) -> State:
    """``asimov.io.RobotState`` -> :class:`State`. Pure; shared with tests."""
    n = len(msg.joint_pos)
    names = joint_names if joint_names and len(joint_names) == n else None
    vel, cur, temp = msg.joint_vel, msg.joint_current, msg.joint_temp
    joints = tuple(
        Joint(
            name=names[i] if names else "",
            pos=float(msg.joint_pos[i]),
            vel=float(vel[i]) if i < len(vel) else None,
            current=float(cur[i]) if i < len(cur) else None,
            temp=float(temp[i]) if i < len(temp) else None,
        )
        for i in range(n)
    )
    g = tuple(float(x) for x in msg.projected_gravity)
    w = tuple(float(x) for x in msg.base_ang_vel)
    q = tuple(float(x) for x in msg.base_quat)
    return State(
        mode=Mode.from_wire(msg.current_mode),
        joints=joints,
        gravity=(g[0], g[1], g[2]) if len(g) == 3 else None,
        gyro=(w[0], w[1], w[2]) if len(w) == 3 else None,
        quat=(q[0], q[1], q[2], q[3]) if len(q) == 4 else None,
        error_flags=int(msg.error_flags),
        alerts=tuple(
            Alert(
                id=int(a.id),
                severity=int(a.severity),
                value=float(a.value),
                threshold=float(a.threshold),
                source_id=int(a.source_id),
                first_set_us=int(a.first_set_us),
            )
            for a in msg.active_alerts
        ),
        battery=_battery_from(msg),
        sequence=int(msg.sequence),
        fw_timestamp_us=int(msg.timestamp_us),
        protocol_version=int(msg.protocol_version),
        edge_timestamp_us=edge_timestamp_us,
    )


def decode_state(
    payload: bytes,
    joint_names: tuple[str, ...] | None = None,
    edge_timestamp_us: int | None = None,
) -> State:
    """Raw wire bytes -> :class:`State`. Raises whatever protobuf raises on garbage; the
    callers treat that as a dropped packet, never as a dead link. ``edge_timestamp_us`` is
    the data-track frame's ``user_timestamp`` when the lane carries one."""
    _, _, st = _pb()
    msg = st.RobotState()
    msg.ParseFromString(payload)
    return state_from_robot_state(msg, joint_names, edge_timestamp_us or 0)


def _battery_from(msg: Any) -> Battery | None:
    """``RobotState.battery`` (field 28). The firmware leaves it absent or all-zero when no
    BMS is fitted; both mean "not reported" here, never a 0 V pack."""
    if not msg.HasField("battery"):
        return None
    b = msg.battery
    if not (b.voltage_v or b.current_a or b.soc_percent or b.max_cell_temp_c or b.protection_flags):
        return None
    return Battery(
        voltage_v=float(b.voltage_v),
        current_a=float(b.current_a),
        soc_percent=float(b.soc_percent),
        max_cell_temp_c=float(b.max_cell_temp_c),
        protection=BatteryProtection(int(b.protection_flags)),
    )
