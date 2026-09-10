"""Connect, and read what the robot says about itself — before commanding anything.

Run against a rig:   menlo-studio up --container --sdk    then   python 01_connect_and_read.py
Against a real robot: its edge must run with --udp-control --udp-state-host <this machine>.
"""

import sys

from asimov_sdk import Robot

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot.connect(host) as robot:
    print(robot.info)  # transport, endpoint, dof, protocol version
    s = robot.state
    print(f"mode      {s.mode.name}")
    print(f"upright   {s.upright}")
    print(f"age       {s.age_s:.3f}s  (how old this sample is)")
    print(f"gravity   {s.gravity}")
    print(f"faulted   {s.faulted}   alerts={len(s.alerts)}")
    print(f"{len(s.joints)} joints, firmware order:")
    for j in s.joints[:6]:
        vel = f"{j.vel:+.3f} rad/s" if j.vel is not None else "vel n/a"
        print(f"  {j.name:18} {j.pos:+.3f} rad   {vel}")
    print("  …")
    knee = s.joint("L_Knee")  # by name; a typo raises KeyError rather than indexing the wrong joint
    print(f"L_Knee via name lookup: {knee.pos:+.3f} rad")
