"""Three ways to stop, and why they are different verbs.

  stop()   zero velocity — the robot keeps its feet (MOVE mode at rest). Almost always right.
  damp()   motors compliant NOW — a standing biped folds. The emergency stop.
  close()  what the `with` block does on exit: zero velocity (if one is held), then drop
           the link. Never a damp, so a script that ends leaves the robot on its feet.

A DAMP drops the robot; the fall latches a fault that suppresses STAND until the firmware
restarts, so run this one last.
"""

import sys
import time

from menlo.asimov import ConnectionConfig, Mode, Robot, UdpConfig

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot(ConnectionConfig(udp=UdpConfig(host))).connect("udp") as robot:
    robot.stand()
    robot.wait_for(Mode.STAND, timeout=15.0)

    robot.set_velocity(vx=0.2)
    robot.wait_for(Mode.MOVE, timeout=5.0)
    time.sleep(1.0)  # the hold keeps it walking while we do nothing

    print("stop()  → zero velocity, stays on its feet (mode stays MOVE)")
    robot.stop()
    time.sleep(1.0)
    print(f"  mode={robot.state.mode.name} upright={robot.state.upright}")

    print("damp()  → collapses; the emergency verb")
    robot.damp()
    s = robot.wait_for(Mode.DAMP, timeout=5.0)
    print(f"  mode={s.mode.name}")
print("left the with-block: close() sent nothing more — nothing was held")
