"""Save one frame from the robot's camera through its LiveKit room, without the SDK.

Uses only livekit. The robot publishes its camera as an ordinary video track; the frame is
saved as a PPM image, which needs no imaging library. Set MANAGER_URL in manager_token.py.
Run: MENLO_CREDENTIAL=... python examples/livekit_raw/camera.py
"""

import asyncio
from pathlib import Path

from livekit import rtc
from manager_token import fetch_grant

PATH = "frame.ppm"
TIMEOUT_S = 10.0


# region main
async def save_one_frame(track: rtc.Track, done: asyncio.Future[str]) -> None:
    stream = rtc.VideoStream(track)
    async for event in stream:
        rgb = event.frame.convert(rtc.VideoBufferType.RGB24)
        header = f"P6 {rgb.width} {rgb.height} 255\n".encode()
        Path(PATH).write_bytes(header + bytes(rgb.data))
        done.set_result(f"saved {PATH}, {rgb.width}x{rgb.height}")
        break
    await stream.aclose()


async def main() -> None:
    grant = fetch_grant()
    room = rtc.Room()
    done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    tasks: set[asyncio.Task[None]] = set()

    def on_track(track: rtc.Track, *_: object) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO and not tasks:
            tasks.add(asyncio.ensure_future(save_one_frame(track, done)))

    room.on("track_subscribed", on_track)
    await room.connect(grant["url"], grant["token"])
    try:
        print(await asyncio.wait_for(done, TIMEOUT_S))
    finally:
        await room.disconnect()


# endregion

if __name__ == "__main__":
    asyncio.run(main())
