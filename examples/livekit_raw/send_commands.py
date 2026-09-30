"""Send STAND to a robot in DAMP through its LiveKit room, without the SDK.

Uses only livekit and asimov-protocol. The script reads the "state" data track first and sends
nothing unless the same checks robot.preflight("stand") makes pass: fresh state, the same
protocol version, no fault or critical alert, battery not protecting and not low, no hot
actuator, robot mode DAMP. The command is one serialized RobotCommand on the "commands" topic.
The robot must be on its feet, hanging from its gantry hook. Set MANAGER_URL in manager_token.py.
Run: MENLO_CREDENTIAL=... python examples/livekit_raw/send_commands.py
"""

import asyncio
import itertools
import sys
import time
from typing import Any

from asimov_protocol.v1 import asimov_command_pb2, asimov_common_pb2, asimov_state_pb2
from livekit import rtc
from manager_token import fetch_grant

PROTOCOL_VERSION = 1
MAX_STATE_AGE_S = 0.5  # decide on a sample at most this old
BATTERY_LOW_PERCENT = 20.0  # the SDK's preflight threshold
JOINT_HOT_C = 60.0  # the SDK's preflight threshold; the firmware latches DAMP at 80 °C
LISTEN_S = 2.0  # how long to read state before deciding

SEQUENCE = itertools.count(1)


def command(mode: asimov_common_pb2.ControlMode) -> bytes:
    """One RobotCommand: protocol version, a sequence number and the sender's clock."""
    msg = asimov_command_pb2.RobotCommand(protocol_version=PROTOCOL_VERSION, mode=mode)
    msg.sequence = next(SEQUENCE)
    msg.timestamp_us = time.time_ns() // 1000
    return bytes(msg.SerializeToString())


def why_not_stand(state: Any, received_at: float) -> str | None:
    """The reason STAND must not be sent now, or None."""
    if time.monotonic() - received_at > MAX_STATE_AGE_S:
        return f"the latest state is older than {MAX_STATE_AGE_S} s"
    if state.protocol_version != PROTOCOL_VERSION:
        return f"the robot speaks protocol {state.protocol_version}, not {PROTOCOL_VERSION}"
    if state.error_flags or any(a.severity == 0 for a in state.active_alerts):
        return f"the robot has a critical alert or a fault (error_flags {state.error_flags:#x})"
    b = state.battery  # absent or all zero: not reported, which the SDK only warns about
    fields = (b.voltage_v, b.current_a, b.soc_percent, b.max_cell_temp_c, b.protection_flags)
    if state.HasField("battery") and any(fields):
        if b.protection_flags:
            return f"the battery is protecting the pack (flags {b.protection_flags:#x})"
        if b.soc_percent < BATTERY_LOW_PERCENT:
            return f"the battery is at {b.soc_percent:.0f} %"
    if any(t >= JOINT_HOT_C for t in state.joint_temp):
        return f"an actuator is at {max(state.joint_temp):.0f} °C"
    if state.current_mode != asimov_common_pb2.CONTROL_MODE_DAMP:
        return "the robot is not in DAMP"
    return None


async def main() -> int:
    grant = fetch_grant()
    room = rtc.Room()
    latest: list[Any] = []  # [RobotState, monotonic time received]
    readers: set[asyncio.Task[None]] = set()

    async def read_state(track: rtc.RemoteDataTrack) -> None:
        async for frame in track.subscribe():
            latest[:] = [
                asimov_state_pb2.RobotState.FromString(bytes(frame.payload)),
                time.monotonic(),
            ]

    def on_data_track(track: rtc.RemoteDataTrack) -> None:
        if track.info.name == "state":
            task = asyncio.ensure_future(read_state(track))
            readers.add(task)
            task.add_done_callback(readers.discard)

    room.on("data_track_published", on_data_track)
    await room.connect(grant["url"], grant["token"])
    try:
        await asyncio.sleep(LISTEN_S)
        # region main
        reason = why_not_stand(*latest) if latest else "no state received"
        if reason is not None:
            print("not sending STAND:", reason)
            return 1
        payload = command(asimov_common_pb2.CONTROL_MODE_STAND)
        await room.local_participant.publish_data(payload, reliable=True, topic="commands")
        # endregion
        await asyncio.sleep(1.0)
        print("robot mode now", asimov_common_pb2.ControlMode.Name(latest[0].current_mode))
    finally:
        await room.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
