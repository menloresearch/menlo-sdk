"""Stand, then walk — waiting on the robot's own report, never on a guessed sleep.

`stand()` returns as soon as the command is sent. `wait_for(Mode.STAND)` returns when
the FIRMWARE reports standing, and raises early if it faults or stops talking instead.

Note where this ends: in MOVE at zero velocity, not back in STAND. STAND is a stiffen
with no balance loop, and asking a free-standing biped for it after a walk tips it over.
Zero velocity in MOVE is how a robot that nothing is holding stands still.
"""

import sys
import time

from menlo.asimov import ConnectionConfig, Mode, Robot, UdpConfig

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot(ConnectionConfig(udp=UdpConfig(host))).connect("udp") as robot:
    if robot.state.mode is not Mode.DAMP:
        # A previous run left it balancing in MOVE. STAND would stiffen it and tip it over;
        # DAMP it (supported) or restart the firmware, then run this again.
        sys.exit(f"robot is {robot.state.mode.name}, not DAMP — start this from DAMP")
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
    # And that is where a walk should end. The robot is balancing under the policy.
    #
    # This example used to finish with stand(). Do not: STAND stiffens every joint to a
    # fixed pose with no balance loop, so a free-standing biped asked to stiffen after
    # walking tips over — and the fall latches a fault-DAMP until the firmware restarts.
    # stand() is the wake-up verb, or for a robot that is held or on its stand.
    print(f"  still upright, still balancing: upright={robot.state.upright}")
