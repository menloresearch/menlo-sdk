"""Print the robot's state from its LiveKit room for 5 s, without the SDK. Sends nothing.

Uses only livekit and asimov-protocol. The robot publishes one serialized RobotState per
frame on the data track named "state". Set MANAGER_URL in manager_token.py.
Run: MENLO_CREDENTIAL=... python examples/livekit_raw/read_state.py
"""

import asyncio

from asimov_protocol.v1 import asimov_common_pb2, asimov_state_pb2
from livekit import rtc
from manager_token import fetch_grant

SECONDS = 5.0


# region main
async def print_states(track: rtc.RemoteDataTrack) -> None:
    async for frame in track.subscribe():
        state = asimov_state_pb2.RobotState.FromString(bytes(frame.payload))
        mode = asimov_common_pb2.ControlMode.Name(state.current_mode)
        print(f"{mode}  protocol {state.protocol_version}  sequence {state.sequence}")


async def main() -> None:
    grant = fetch_grant()
    room = rtc.Room()
    readers: set[asyncio.Task[None]] = set()

    def on_data_track(track: rtc.RemoteDataTrack) -> None:
        if track.info.name == "state":
            task = asyncio.ensure_future(print_states(track))
            readers.add(task)  # keep a reference so the task is not collected
            task.add_done_callback(readers.discard)

    room.on("data_track_published", on_data_track)
    await room.connect(grant["url"], grant["token"])
    try:
        await asyncio.sleep(SECONDS)
    finally:
        await room.disconnect()


# endregion

if __name__ == "__main__":
    asyncio.run(main())
