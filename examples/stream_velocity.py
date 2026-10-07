"""Send velocity from your own loop: one packet per call, at the rate you choose.

set_velocity(hold=False) sends one velocity packet and re-sends nothing: your loop is the
clock. Here the loop runs at RATE_HZ for DURATION_S, easing forward up to VX and back to
zero, then balance() leaves the robot in MOVE, balancing in place. If a loop like this stops,
nothing re-sends its last velocity: on udp and hybrid, Asimov Edge zeroes velocity 2 s after
the last packet. Run stand.py and balance.py first: this script streams in MOVE only, a rule
of its own. The robot must hang from its gantry hook, with 2 m of clear floor ahead. To
stop first on your own rule, uncomment the two guard lines (see guard.py).
Run: python examples/stream_velocity.py
"""

import math
import sys
import time

# from guard import guard  # optional: your own rule, see guard.py
from menlo.asimov import Mode, NotReadyError, Robot

RATE_HZ = 50.0
DURATION_S = 2.0
VX = 0.2  # m/s forward at the peak; the firmware caps it at 0.4 m/s

with Robot().connect() as robot:
    # region main
    # guard(robot)  # optional: uncomment this and the import to stop on your rule
    mode = robot.get_state().mode
    if mode is not Mode.MOVE:  # this script's own rule: stream from MOVE only
        print(f"robot mode {mode.name}: this script streams in MOVE only; run balance.py first")
        sys.exit(1)
    period = 1.0 / RATE_HZ
    start = time.monotonic()
    packets = 0
    try:
        while (t := time.monotonic() - start) < DURATION_S:
            vx = VX * math.sin(math.pi * t / DURATION_S)  # up to VX, then back to zero
            robot.set_velocity(vx=vx, hold=False)  # one packet; no delay on a live stream
            packets += 1
            time.sleep(period - (time.monotonic() - start) % period)  # the next tick
    except NotReadyError as exc:  # no live state: nothing was sent in this call
        print(exc)
        sys.exit(1)
    robot.balance()  # zero velocity: the robot stays in MOVE and balances in place
    mode = robot.get_state().mode
    print(f"sent {packets} packets in {DURATION_S:.0f} s; robot mode {mode.name}")
    # endregion
