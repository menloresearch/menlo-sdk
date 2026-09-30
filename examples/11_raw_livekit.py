"""Read the robot's state and send one command through its LiveKit room, without the SDK.

Uses only livekit and asimov-protocol: a token from Asimov Manager, RobotState frames on the
"state" data track, RobotCommand packets on the "commands" topic. --stand sends STAND from DAMP
after the blocking checks robot.preflight("stand") makes: fresh state, same protocol version, no
fault or critical alert, battery not protecting and not low, no hot actuator.
Run: MENLO_CREDENTIAL=... python examples/11_raw_livekit.py http://192.168.22.32 [--stand]
"""

import argparse
import asyncio
import itertools
import json
import os
import sys
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from asimov_protocol.v1 import asimov_command_pb2, asimov_common_pb2, asimov_state_pb2
from livekit import rtc

SEQUENCE = itertools.count(1)
PROTOCOL_VERSION = 1
MAX_STATE_AGE_S = 0.5  # decide on a sample at most this old
BATTERY_LOW_PERCENT = 20.0  # the SDK's preflight threshold
JOINT_HOT_C = 60.0  # the SDK's preflight threshold; the firmware latches DAMP at 80 C


# region token
class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib would re-send the credential to the new URL."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def fetch_grant(manager: str, credential: str) -> dict[str, Any]:
    """Ask Asimov Manager for the LiveKit URL, the robot's room and a join token."""
    request = urllib.request.Request(
        manager.rstrip("/") + "/api/livekit/token",
        data=b"{}",
        method="POST",
        headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
        grant: dict[str, Any] = json.load(response)  # url, room, token, identity, role
    # endregion
    # The URL is the one Asimov Edge uses on the robot; localhost there is the manager's host.
    url = urlsplit(grant["url"])
    if url.hostname in ("localhost", "127.0.0.1"):
        host = urlsplit(manager).hostname or ""
        netloc = host if url.port is None else f"{host}:{url.port}"
        grant["url"] = url._replace(netloc=netloc).geturl()
    return grant


# region command
def command(mode: asimov_common_pb2.ControlMode) -> bytes:
    """One RobotCommand: protocol version 1, a sequence number, and the sender's clock."""
    msg = asimov_command_pb2.RobotCommand(protocol_version=PROTOCOL_VERSION, mode=mode)
    msg.sequence = next(SEQUENCE)
    msg.timestamp_us = time.time_ns() // 1000
    return bytes(msg.SerializeToString())


# endregion


def why_not_stand(latest: list[Any]) -> str | None:
    """The reason STAND must not be sent now, or None."""
    if not latest:
        return "no state received"
    state, received_at = latest
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


async def main(manager: str, credential: str, stand: bool) -> int:
    grant = fetch_grant(manager, credential)
    room = rtc.Room()
    latest: list[Any] = []
    readers: set[asyncio.Task[None]] = set()

    # region state
    async def read_state(track: rtc.RemoteDataTrack) -> None:
        async for frame in track.subscribe():
            state = asimov_state_pb2.RobotState.FromString(bytes(frame.payload))
            latest[:] = [state, time.monotonic()]

    def on_data_track(track: rtc.RemoteDataTrack) -> None:
        if track.info.name == "state":
            task = asyncio.ensure_future(read_state(track))
            readers.add(task)
            task.add_done_callback(readers.discard)

    room.on("data_track_published", on_data_track)
    await room.connect(grant["url"], grant["token"])
    # endregion
    try:
        for _ in range(10):
            await asyncio.sleep(0.5)
            if latest:
                mode = asimov_common_pb2.ControlMode.Name(latest[0].current_mode)
                print(f"{mode}  protocol {latest[0].protocol_version}  seq {latest[0].sequence}")
        # region send
        if stand:
            if (reason := why_not_stand(latest)) is not None:
                print("not sending STAND:", reason)
                return 1
            payload = command(asimov_common_pb2.CONTROL_MODE_STAND)
            await room.local_participant.publish_data(payload, reliable=True, topic="commands")
            await asyncio.sleep(1.0)
            print("now", asimov_common_pb2.ControlMode.Name(latest[0].current_mode))
        # endregion
    finally:
        await room.disconnect()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Use the robot's LiveKit room without the SDK.")
    parser.add_argument("manager", help="the robot's Asimov Manager URL")
    parser.add_argument("--stand", action="store_true", help="send STAND if the robot is in DAMP")
    args = parser.parse_args()
    credential = os.environ.get("MENLO_CREDENTIAL", "")
    if not credential:
        parser.error("set MENLO_CREDENTIAL to an SDK credential from Asimov Manager")
    sys.exit(asyncio.run(main(args.manager, credential, args.stand)))
