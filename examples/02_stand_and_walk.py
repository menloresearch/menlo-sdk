"""Stand, then walk — waiting on the robot's own report, never on a guessed sleep.

`stand()` returns as soon as the command is sent. `wait_for(Mode.STAND)` returns when
the FIRMWARE reports standing, and raises early if it faults or stops talking instead.
"""

import sys
import time

from asimov_sdk import Mode, Robot

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot.connect_direct(host) as robot:
    print("standing …")
    robot.stand()
    s = robot.wait_for(Mode.STAND, timeout=15.0)
    print(f"  standing (upright={s.upright})")

    print("walking forward at 0.25 m/s for 4 s …")
    sent = robot.set_velocity(vx=0.25, duration=4.0)  # held at 10 Hz, then zeroed for you
    print(f"  sent {sent.command} (clamped={sent.clamped})")
    robot.wait_for(Mode.MOVE, timeout=5.0)
    time.sleep(4.5)  # the bounded hold ends by itself; the SDK sends the zero
    print(f"  hold over: mode={robot.state.mode.name} (MOVE at zero velocity = standing in place)")
    robot.stand()  # zero velocity is not the STAND posture; ask for it
    robot.wait_for(Mode.STAND, timeout=8.0)
    print("  back in STAND")
