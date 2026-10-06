"""Print what the robot reports, the facts a guard reads. Sends nothing.

The SDK does not refuse a command because of what the robot reports: a fault, an alert, a
hot actuator or a low battery is for your script to decide on (see guard.py). The one thing
a command waits for, and then refuses without, is live state. robot.preflight(action) lists
the same facts: a blocking problem means no live state; the rest are information.
Run: python examples/check.py
"""

import time

from menlo.asimov import Robot

with Robot().connect() as robot:
    # region main
    # The SDK counts the 0.5 s a robot in STAND must be upright to arm from its own samples.
    time.sleep(0.6)
    s = robot.get_state()  # one sample: every fact below comes from it
    print(f"robot mode {s.mode.name}, armed {robot.armed}, faulted {s.faulted}")
    print(f"alerts {', '.join(a.name for a in s.alerts) or 'none'}")
    temps = [j.temp for j in s.joints if j.temp is not None]
    print(f"hottest joint {max(temps):.0f} C" if temps else "joint temperatures not reported")
    print(f"battery {s.battery.soc_percent:.0f} %" if s.battery else "battery not reported")
    print(f"state {s.age_s:.2f} s old")
    for action in ("stand", "move", "trajectory"):
        print(robot.preflight(action))  # "ready to move" means live state, and the facts
    # endregion
