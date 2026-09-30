"""Record a few seconds from the robot's microphone through its LiveKit room, without the SDK.

Uses only livekit and the standard library. The robot publishes its microphone as an ordinary
audio track of 16-bit PCM; the recording is saved as a WAV file. Set MANAGER_URL in
manager_token.py.
Run: MENLO_CREDENTIAL=... python examples/livekit_raw/audio.py
"""

import asyncio
import wave

from livekit import rtc
from manager_token import fetch_grant

PATH = "microphone.wav"
SECONDS = 3.0
TIMEOUT_S = 10.0


# region main
async def record(track: rtc.Track, done: asyncio.Future[str]) -> None:
    stream = rtc.AudioStream(track)
    chunks: list[bytes] = []
    rate = channels = 0
    async for event in stream:
        frame = event.frame
        rate, channels = frame.sample_rate, frame.num_channels
        chunks.append(bytes(frame.data))
        if sum(len(c) for c in chunks) >= SECONDS * rate * channels * 2:
            break
    await stream.aclose()
    with wave.open(PATH, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)  # 16-bit samples
        wav.setframerate(rate)
        wav.writeframes(b"".join(chunks))
    done.set_result(f"saved {PATH}, {SECONDS:.0f} s at {rate} Hz")


async def main() -> None:
    grant = fetch_grant()
    room = rtc.Room()
    done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    tasks: set[asyncio.Task[None]] = set()

    def on_track(track: rtc.Track, *_: object) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO and not tasks:
            tasks.add(asyncio.ensure_future(record(track, done)))

    room.on("track_subscribed", on_track)
    await room.connect(grant["url"], grant["token"])
    try:
        print(await asyncio.wait_for(done, SECONDS + TIMEOUT_S))
    finally:
        await room.disconnect()


# endregion

if __name__ == "__main__":
    asyncio.run(main())
