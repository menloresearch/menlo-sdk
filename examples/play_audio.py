"""Play a WAV file on the robot's speaker.

The file must hold 16-bit PCM, which most WAV files do; its sample rate and channel count
are sent as they are. Connection mode: hybrid or livekit (udp carries no audio). Sends no
motion command.
Run: python examples/play_audio.py
"""

import sys
import wave

from menlo.asimov import Robot

PATH = "hello.wav"
CHUNK_S = 1.0  # seconds of audio per call; each call returns once the room has taken it

# The speaker works whether or not the firmware is running.
with Robot().connect(require_state=False) as robot:
    # region main
    if not robot.has("speaker"):
        print("this connection carries no speaker; use hybrid or livekit")
        sys.exit(1)
    with wave.open(PATH, "rb") as wav:
        if wav.getsampwidth() != 2:
            print(f"{PATH} is not 16-bit PCM; convert it first")
            sys.exit(1)
        rate, channels = wav.getframerate(), wav.getnchannels()
        pcm = wav.readframes(wav.getnframes())
    # Sent in short pieces, in order: play_pcm() blocks until the room has taken each one,
    # so the file plays at its own pace.
    step = int(rate * CHUNK_S) * 2 * channels
    for start in range(0, len(pcm), step):
        robot.speaker.play_pcm(pcm[start : start + step], sample_rate_hz=rate, channels=channels)
    print(f"played {PATH}, {len(pcm) / (2 * channels * rate):.1f} s at {rate} Hz")
    # endregion
