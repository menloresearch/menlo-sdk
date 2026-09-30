"""Save a photo and a short clip, then play a tone on the robot's speaker.

The clip is saved as one JPEG per frame in CLIP_DIR and its sound as CLIP_WAV.
Connection mode: hybrid or livekit (udp carries no camera or audio). Saving JPEGs needs
Pillow (pip install pillow). Sends no motion command.
Run: python examples/camera_and_audio.py
"""

import math
import struct
from pathlib import Path

from menlo.asimov import Robot

PHOTO = "photo.jpg"
CLIP_DIR = "clip"
CLIP_WAV = "clip.wav"
CLIP_S = 3.0
TONE_HZ = 440.0
TONE_S = 1.0
SAMPLE_RATE_HZ = 16_000

# The camera, microphone and speaker work whether or not the firmware is running.
with Robot().connect(require_state=False) as robot:
    # region main
    if robot.has("camera"):
        photo = robot.camera.photo()  # the next fresh frame, rgb8
        Path(PHOTO).write_bytes(photo.to_jpeg())
        print(f"saved {PHOTO}, {photo.width}x{photo.height}")
        clip = robot.camera.capture_clip(CLIP_S, audio=robot.has("microphone"))
        frames = clip.save_frames(CLIP_DIR)
        print(f"saved {len(frames)} frames to {CLIP_DIR}/ at {clip.fps:.1f} fps")
        if clip.audio:
            print("saved", clip.save_wav(CLIP_WAV))
    else:
        print("this connection carries no camera; use hybrid or livekit")

    if robot.has("speaker"):
        # 16-bit mono PCM: a sine wave at TONE_HZ for TONE_S.
        tone = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * TONE_HZ * i / SAMPLE_RATE_HZ)))
            for i in range(int(SAMPLE_RATE_HZ * TONE_S))
        )
        robot.speaker.play_pcm(tone, sample_rate_hz=SAMPLE_RATE_HZ)
        print(f"played {TONE_S:.0f} s at {TONE_HZ:.0f} Hz")
    # endregion
