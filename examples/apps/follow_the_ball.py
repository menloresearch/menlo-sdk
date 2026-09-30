"""Turn towards a magenta ball in the robot's camera and walk up to it.

  g       start following (or resume)     space   pause, balancing in place
  b       damp (asks first)               x       quit (Ctrl-C also quits)

Nothing moves until you press g. Connection mode: hybrid or livekit (the camera comes over
LiveKit). Needs numpy. Run stand.py first; the robot must be on its feet, hanging from its
gantry hook, with clear floor around it.
Run in a terminal: python examples/apps/follow_the_ball.py
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # check.py and keyboard.py

from check import require_ready
from keyboard import NextKey, confirm_and_damp, stop_if_moving, terminal_keys
from menlo.asimov import Frame, Robot

# Magenta: high red, low green, high blue. Little else in a plain room passes this test.
R_MIN, G_MAX, B_MIN = 140, 90, 140
MIN_PIXELS = 25  # fewer is speckle, not a ball
YAW_GAIN = 1.6  # rad/s per unit of horizontal error
MAX_YAW = 0.6  # rad/s
CRUISE_VX = 0.25  # m/s while the ball is ahead and still small
CENTRED = 0.18  # |error| below this counts as ahead
CLOSE = 0.045  # the ball fills this fraction of the frame: close enough
MAX_FRAME_AGE_S = 0.3  # an older frame says where the ball was, not where it is
HOLD_S = 0.5  # each step moves the robot for this long at most
TICK_S = 0.1


def numpy() -> Any:
    try:
        import numpy as np
    except ImportError:
        sys.exit("This example needs numpy: pip install numpy")
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


def step(robot: Robot) -> None:
    """One step towards the ball, or stand still when it is not in a fresh frame."""
    frame = robot.camera.latest()
    fresh = frame is not None and frame.age_s < MAX_FRAME_AGE_S
    hit = find_ball(frame) if fresh and frame is not None else None
    if hit is None:
        robot.stop()  # not in view, or the view is old: stand still, balancing
        return
    error, area = hit
    vyaw = max(-MAX_YAW, min(MAX_YAW, -YAW_GAIN * error))
    vx = CRUISE_VX if abs(error) < CENTRED and area < CLOSE else 0.0
    # A bounded hold: if this loop stalls, the robot stops after HOLD_S.
    robot.set_velocity(vx=vx, vyaw=vyaw, duration=HOLD_S)


def main(next_key: NextKey) -> None:
    numpy()
    with Robot().connect() as robot:
        if not robot.has("camera"):
            sys.exit("This connection carries no camera; use hybrid or livekit.")
        require_ready(robot, "move")
        print("g start, space pause, b damp, x quit")
        following = False
        try:
            # region main
            while True:
                key = next_key(TICK_S)
                if key in ("x", "\x03"):
                    break
                if key == "g":
                    check = robot.preflight("move")
                    following = check.ok
                    print("following" if following else check)
                elif key == " ":
                    following = False
                    stop_if_moving(robot)
                    print("paused")
                elif key == "b":
                    following = False
                    print(confirm_and_damp(robot, next_key))
                if following:
                    check = robot.preflight("move")
                    if check.ok:
                        step(robot)
                    else:
                        following = False
                        stop_if_moving(robot)
                        print(check)
            # endregion
        except KeyboardInterrupt:
            pass
        finally:
            stop_if_moving(robot)


if __name__ == "__main__":
    with terminal_keys() as keys:
        main(keys)
