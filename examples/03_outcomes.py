"""Was the command admitted? Did it take effect? Two questions, two answers.

Every verb returns a `Sent`. `sent.wait_outcome()` is the arbiter's verdict on THAT command
— Applied, Refused(reason) or Unknown. The UDP lane carries no verdicts, so you
will see Unknown; the code path is the one that lights up when it does. "Did it take
effect" is read from state, and works today.
"""

import sys

from menlo.asimov import (
    Applied,
    ConnectionConfig,
    Mode,
    Refused,
    Robot,
    UdpConfig,
    Unknown,
    WaitTimeoutError,
)

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot(ConnectionConfig(udp=UdpConfig(host))).connect("udp") as robot:
    robot.damp()
    robot.wait_for(Mode.DAMP, timeout=5.0)

    sent = robot.set_velocity(vx=0.3)  # the edge drops velocity while the firmware is DAMPed
    match sent.wait_outcome():
        case Applied():
            print("admitted")
        case Refused(reason=r, detail=d):
            print(f"refused: {r.name} retryable={r.retryable} {d}")
        case Unknown(waited_s=w):
            print(f"no verdict within {w}s — the UDP lane has no outcome channel")

    # The observable truth, on any edge: the robot did not start moving.
    try:
        robot.wait_for(Mode.MOVE, timeout=1.0)
    except WaitTimeoutError as exc:
        print(f"did not take effect: {exc}")
    robot.stop()
