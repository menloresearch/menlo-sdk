"""Save a photo and a 3 s clip with sound, then play a 1 s tone on the robot's speaker.

Connection mode: hybrid or livekit (udp carries no media). The photo needs Pillow.
Run: python examples/09_camera_and_audio.py
"""

import math
import struct
from pathlib import Path

from menlo.asimov import Robot

# Camera, microphone and speaker work whether or not the firmware is running.
with Robot().connect(require_state=False) as robot:
    # region camera
    if robot.has("camera"):
        photo = robot.camera.photo()  # the next fresh frame, rgb8
        Path("photo.jpg").write_bytes(photo.to_jpeg())
        clip = robot.camera.capture_clip(3.0, audio=robot.has("microphone"))
        print(f"{len(clip.frames)} frames at {clip.fps:.1f} fps")
        if clip.audio:
            print("saved", clip.save_wav("clip.wav"))
    else:
        print("this connection carries no camera")
    # endregion

    # region speaker
    if robot.has("speaker"):
        rate = 16_000
        tone = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(rate)
        )
        robot.speaker.play_pcm(tone, sample_rate_hz=rate)
    # endregion
