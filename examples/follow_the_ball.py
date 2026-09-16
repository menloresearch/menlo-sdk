#!/usr/bin/env python3
"""Follow the ball — steer the robot with nothing but its own camera.

Ordinary Python, watching the robot's camera over LiveKit and driving it, with no
platform anywhere in the loop. The commands go into asimov-edge on the same topic the
cockpit uses and land on the same arbiter and the same safety layer, so the robot keeps
every watchdog it has.

The target is the magenta ball from Menlo Studio's ``chase_ball`` scene: a 0.30 m sphere
orbiting the robot at 2.0 m and 0.4 rad/s (a 15.71 s lap at 0.8 m/s). It is the only
saturated colour in a grey world, which is the whole reason it is that colour.

    # the rig (from mono):
    menlo-studio up --container --with-edge --sdk --scene chase_ball

    # this script, either mode:
    python examples/follow_the_ball.py --mode hybrid  --token "$TOK"
    python examples/follow_the_ball.py --mode livekit --token "$TOK"

The ball laps at 0.8 m/s and the robot walks at a fraction of that, so catching it is not
the goal and never was: the robot turns to hold it in frame and closes when it is ahead.
What the run proves is that a frame off the robot's camera moved a real motor.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from asimov_sdk import Mode, Robot

# ── the target, straight from assets/scenes/chase_ball/README.md ──────────────────────
# Magenta is RGBA "1 0 1 1" with emission 0.8. In RGB that is a high red, a near-zero
# green and a high blue — a test no other pixel in a grey chequered world passes, which
# is cheaper and steadier here than an HSV conversion and keeps numpy as the only import.
R_MIN, G_MAX, B_MIN = 140, 90, 140
MIN_PIXELS = 25  # ~45 px tall at 2 m, so a few hundred px of blob; 25 rejects speckle

# ── control ──────────────────────────────────────────────────────────────────────────
YAW_GAIN = 1.6  # rad/s per unit of normalised horizontal error
MAX_YAW = 0.8  # rad/s
CRUISE_VX = 0.25  # m/s when the ball is ahead and far
CENTRED = 0.18  # |error| under this counts as "ahead"
BIG_BLOB = 0.045  # blob area as a fraction of frame ⇒ close enough, stop closing
TICK_HZ = 10.0


def find_ball(frame) -> tuple[float, float] | None:
    """Return (horizontal error in [-1, 1], blob area as a fraction of the frame).

    ``None`` when the ball is not in view — which is most of a lap, and is a normal
    reading, not an error.
    """
    a = frame.to_numpy()  # rgb8 → (h, w, 3)
    r = a[:, :, 0].astype(np.int16)
    g = a[:, :, 1].astype(np.int16)
    b = a[:, :, 2].astype(np.int16)
    mask = (r > R_MIN) & (g < G_MAX) & (b > B_MIN)
    count = int(mask.sum())
    if count < MIN_PIXELS:
        return None
    xs = np.nonzero(mask.any(axis=0))[0]
    centre = float(xs.mean())
    width = mask.shape[1]
    error = (centre - width / 2.0) / (width / 2.0)
    return error, count / float(mask.size)


def connect(args) -> Robot:
    if args.mode == "udp":
        return Robot.connect(args.host, command_port=args.command_port, timeout=args.timeout)
    if args.mode == "hybrid":
        return Robot.connect_hybrid(
            args.host,
            livekit_url=args.livekit_url,
            room=args.room,
            token=args.token,
            command_port=args.command_port,
            timeout=args.timeout,
        )
    return Robot.connect_livekit(
        args.livekit_url, args.room, token=args.token, timeout=args.timeout
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("hybrid", "livekit", "udp"), default="hybrid")
    p.add_argument("--host", default="127.0.0.1", help="the edge's UDP lane")
    p.add_argument(
        "--command-port", type=int, default=8850, help="UDP command port (studio: 18850)"
    )
    p.add_argument("--livekit-url", default="ws://127.0.0.1:7880")
    p.add_argument("--room", default="robot-demo-001")
    p.add_argument("--token", default=None, help="from the robot's manager, or `lk token create`")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--timeout", type=float, default=25.0)
    args = p.parse_args()

    if args.mode != "udp" and not args.token:
        p.error("--token is required for hybrid and livekit modes (the SDK never mints one)")

    robot = connect(args)
    print(f"connected: {robot.info.dof} joints over {robot.info.transport}", flush=True)
    if "camera" not in robot.info.capabilities:
        print("this robot publishes no camera — nothing to steer on", file=sys.stderr)
        robot.close()
        return 2

    seen = ticks = 0
    errors: list[float] = []
    biggest = 0.0

    try:
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=10)
        print("standing; watching for the ball", flush=True)

        interval = 1.0 / TICK_HZ
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            tick_started = time.monotonic()
            ticks += 1

            frame = robot.camera.latest()
            hit = find_ball(frame) if frame is not None else None

            if hit is None:
                # Not in view: hold still rather than guess which way it went. The ball
                # comes back every 15.71 s on its own.
                robot.set_velocity(0.0, 0.0, 0.0)
            else:
                error, area = hit
                seen += 1
                errors.append(abs(error))
                biggest = max(biggest, area)
                vyaw = max(-MAX_YAW, min(MAX_YAW, -YAW_GAIN * error))
                close_enough = area >= BIG_BLOB
                vx = CRUISE_VX if (abs(error) < CENTRED and not close_enough) else 0.0
                robot.set_velocity(vx, 0.0, vyaw)

            time.sleep(max(0.0, interval - (time.monotonic() - tick_started)))

        robot.stop()
        robot.damp()
    finally:
        try:
            robot.stop()
        finally:
            robot.close()

    pct = (100.0 * seen / ticks) if ticks else 0.0
    mean_err = (sum(errors) / len(errors)) if errors else float("nan")
    print(
        f"\nticks {ticks} · ball in frame {seen} ({pct:.0f}%) · "
        f"mean centring error {mean_err:.2f} · largest blob {biggest * 100:.1f}% of frame",
        flush=True,
    )
    # One lap is 15.71 s and the ball is in the 90° head_cam for roughly a quarter of it,
    # so a healthy run sees it in the low tens of percent, centred well under 0.5.
    laps = args.seconds / 15.71
    print(f"({laps:.1f} orbits elapsed)", flush=True)
    return 0 if seen else 1


if __name__ == "__main__":
    raise SystemExit(main())
