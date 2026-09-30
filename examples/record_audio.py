"""Record a few seconds from the robot's microphone and save them as a WAV file.

Connection mode: hybrid or livekit (udp carries no audio). Sends no motion command.
Run: python examples/record_audio.py
"""

import sys
import wave

from menlo.asimov import AudioChunk, Robot

PATH = "microphone.wav"
SECONDS = 3.0

# The microphone works whether or not the firmware is running.
with Robot().connect(require_state=False) as robot:
    # region main
    if not robot.has("microphone"):
        print("this connection carries no microphone; use hybrid or livekit")
        sys.exit(1)
    # Every chunk in order, as 16-bit PCM. chunks() raises WaitTimeoutError when the
    # microphone says nothing for its timeout.
    chunks: list[AudioChunk] = []
    recorded_s = 0.0
    for chunk in robot.microphone.chunks(timeout=5.0):
        chunks.append(chunk)
        recorded_s += chunk.duration_s
        if recorded_s >= SECONDS:
            break
    with wave.open(PATH, "wb") as wav:
        wav.setnchannels(chunks[0].channels)
        wav.setsampwidth(2)  # 16-bit samples
        wav.setframerate(chunks[0].sample_rate_hz)
        wav.writeframes(b"".join(c.data for c in chunks))
    print(f"saved {PATH}, {recorded_s:.1f} s at {chunks[0].sample_rate_hz} Hz")
    # endregion
