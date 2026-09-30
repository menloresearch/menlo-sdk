"""Turn towards a magenta ball in the robot's camera and walk to it, for 60 s.

Connection mode: hybrid or livekit (the camera comes over LiveKit). Needs numpy. The robot must
be on its feet with clear floor around it. Run: python examples/apps/follow_the_ball.py
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Any

from menlo.asimov import Frame, Mode, Robot

# Magenta: high red, low green, high blue. Nothing else in a plain room passes this test.
R_MIN, G_MAX, B_MIN = 140, 90, 140
MIN_PIXELS = 25  # fewer is speckle, not a ball
YAW_GAIN = 1.6  # rad/s per unit of horizontal error
MAX_YAW = 0.6  # rad/s
CRUISE_VX = 0.25  # m/s while the ball is ahead and still small
CENTRED = 0.18  # |error| below this counts as ahead
CLOSE = 0.045  # the ball fills this fraction of the frame: close enough
MAX_FRAME_AGE_S = 0.3  # an older frame says where the ball was, not where it is
TICK_S = 0.1
MAX_SECONDS = 600.0  # a run is bounded: at most 10 minutes


def numpy() -> Any:
    try:
        import numpy as np
    except ImportError:
        sys.exit("this example needs numpy: pip install numpy")
    return np


def find_ball(frame: Frame) -> tuple[float, float] | None:
    """(horizontal error in [-1, 1], area as a fraction of the frame), or None if not seen."""
    np = numpy()
    a = frame.to_numpy().astype(np.int16)  # (height, width, 3), rgb8
    mask = (a[:, :, 0] > R_MIN) & (a[:, :, 1] < G_MAX) & (a[:, :, 2] > B_MIN)
    if int(mask.sum()) < MIN_PIXELS:
        return None
    centre = float(np.nonzero(mask.any(axis=0))[0].mean())
    half = mask.shape[1] / 2.0
    return (centre - half) / half, float(mask.sum()) / mask.size


def follow(robot: Robot, seconds: float) -> None:
    # region follow
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        frame = robot.camera.latest()
        fresh = frame is not None and frame.age_s < MAX_FRAME_AGE_S
        hit = find_ball(frame) if fresh and frame is not None else None
        if hit is None:
            robot.stop()  # not in view, or the view is old: stand still, balancing
        else:
            error, area = hit
            vyaw = max(-MAX_YAW, min(MAX_YAW, -YAW_GAIN * error))
            vx = CRUISE_VX if abs(error) < CENTRED and area < CLOSE else 0.0
            # Each tick holds for 0.5 s at most, so a stalled loop stops the robot.
            robot.set_velocity(vx=vx, vyaw=vyaw, duration=0.5)
        time.sleep(TICK_S)
    robot.stop()
    # endregion


def main() -> int:
    parser = argparse.ArgumentParser(description="Follow a magenta ball with the camera.")
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()
    if not (math.isfinite(args.seconds) and 0 < args.seconds <= MAX_SECONDS):
        parser.error(f"--seconds must be more than 0 and at most {MAX_SECONDS:.0f}")
    numpy()

    with Robot().connect() as robot:
        if not robot.has("camera"):
            print("this connection carries no camera; use hybrid or livekit", file=sys.stderr)
            return 2
        if robot.state.mode is Mode.DAMP:
            robot.wait_ready("stand")
            robot.stand()
            robot.wait_for(Mode.STAND)
        robot.wait_ready("move")
        follow(robot, args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
