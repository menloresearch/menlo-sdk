#!/usr/bin/env python3
"""Record the follow-the-ball run: the robot's own camera, with the detection it steers on.

Writes raw RGB frames to stdout for ffmpeg. Overlay is drawn with numpy only (no PIL):
a box around the magenta blob, a centre reticle, and a bar showing the yaw command.
"""

from __future__ import annotations

import sys
import time

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "examples")
from asimov_sdk import Mode, Robot  # noqa: E402
from follow_the_ball import (  # noqa: E402
    BIG_BLOB,
    B_MIN,
    CENTRED,
    CRUISE_VX,
    G_MAX,
    MAX_YAW,
    MIN_PIXELS,
    R_MIN,
    YAW_GAIN,
)

TOKEN = sys.argv[1]
ROOM = sys.argv[2]
SECONDS = float(sys.argv[3])
PORT = int(sys.argv[4])
FPS = 15


def box(img, x0, y0, x1, y1, colour, t=3):
    h, w, _ = img.shape
    x0, x1 = max(0, x0), min(w - 1, x1)
    y0, y1 = max(0, y0), min(h - 1, y1)
    img[y0 : y0 + t, x0:x1] = colour
    img[max(0, y1 - t) : y1, x0:x1] = colour
    img[y0:y1, x0 : x0 + t] = colour
    img[y0:y1, max(0, x1 - t) : x1] = colour


def main() -> int:
    r = Robot.connect_hybrid(
        "127.0.0.1",
        livekit_url="ws://127.0.0.1:7880",
        room=ROOM,
        token=TOKEN,
        command_port=PORT,
        timeout=30,
    )
    print(f"recording: {r.info.dof} joints over {r.info.transport}", file=sys.stderr, flush=True)
    r.stand()
    try:
        r.wait_for(Mode.STAND, timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"stand: {exc}", file=sys.stderr, flush=True)

    out = sys.stdout.buffer
    interval = 1.0 / FPS
    deadline = time.monotonic() + SECONDS
    seen = ticks = 0

    while time.monotonic() < deadline:
        started = time.monotonic()
        ticks += 1
        f = r.camera.latest()
        if f is None:
            time.sleep(interval)
            continue
        img = f.to_numpy().copy()
        h, w, _ = img.shape

        rr = img[:, :, 0].astype(np.int16)
        gg = img[:, :, 1].astype(np.int16)
        bb = img[:, :, 2].astype(np.int16)
        mask = (rr > R_MIN) & (gg < G_MAX) & (bb > B_MIN)
        count = int(mask.sum())

        # centre reticle — where the robot is pointing
        img[h // 2 - 1 : h // 2 + 1, w // 2 - 22 : w // 2 + 22] = (90, 90, 90)
        img[h // 2 - 22 : h // 2 + 22, w // 2 - 1 : w // 2 + 1] = (90, 90, 90)

        vyaw = 0.0
        if count >= MIN_PIXELS:
            seen += 1
            ys, xs = np.nonzero(mask)
            box(img, int(xs.min()) - 6, int(ys.min()) - 6, int(xs.max()) + 6, int(ys.max()) + 6,
                (0, 255, 90))
            cx = float(xs.mean())
            err = (cx - w / 2.0) / (w / 2.0)
            vyaw = max(-MAX_YAW, min(MAX_YAW, -YAW_GAIN * err))
            area = count / float(mask.size)
            vx = CRUISE_VX if (abs(err) < CENTRED and area < BIG_BLOB) else 0.0
            r.set_velocity(vx, 0.0, vyaw)
            # tick marking the blob's horizontal centre
            img[h - 26 : h - 8, max(0, int(cx) - 2) : int(cx) + 2] = (0, 255, 90)
        else:
            r.set_velocity(0.0, 0.0, 0.0)

        # yaw command bar, drawn from the centre of the bottom edge
        span = int((vyaw / MAX_YAW) * (w * 0.35))
        x0, x1 = (w // 2, w // 2 + span) if span >= 0 else (w // 2 + span, w // 2)
        img[h - 7 : h - 3, min(x0, x1) : max(x0, x1) + 1] = (255, 200, 0)
        img[h - 10 : h - 1, w // 2 - 1 : w // 2 + 1] = (220, 220, 220)

        out.write(img.astype(np.uint8).tobytes())
        time.sleep(max(0.0, interval - (time.monotonic() - started)))

    r.stop()
    r.close()
    pct = 100.0 * seen / ticks if ticks else 0
    print(f"frames {ticks} · ball seen {seen} ({pct:.0f}%)", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
