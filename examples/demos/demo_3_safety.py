"""Demo 3 — Safety: what happens when things go wrong, and how stopping works.

Three things every robot SDK gets asked and few answer honestly:

  1. Was my command admitted?      -> `Sent.wait_outcome()`: Applied | Refused | Unknown
  2. Did it take effect?           -> `wait_for` / `wait_until` on the robot's own state
  3. How do I stop?                -> stop() keeps the feet; damp() is the emergency verb;
                                      close() zeroes a held velocity on every exit path

This script ends in DAMP, so on the simulator run it LAST (a DAMP mid-session
suppresses the next STAND until the rig restarts).

    python demo_3_safety.py [host]
"""

import sys
import time

from asimov_sdk import Applied, Mode, Refused, Robot, Unknown, WaitTimedOut

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot.connect_direct(host) as robot:
    print("1) a command sent while the firmware is DAMPed")
    robot.damp()
    robot.wait_for(Mode.DAMP, timeout=5.0)
    sent = robot.set_velocity(vx=0.3)  # the edge's safety layer drops velocity while DAMPed
    match sent.wait_outcome():
        case Applied():
            print("   admitted")
        case Refused(reason=r):
            print(f"   refused: {r.name} (retryable={r.retryable})")
        case Unknown(waited_s=w):
            print(f"   no verdict in {w}s — this edge has no outcome channel yet; observe instead:")
    try:
        robot.wait_for(Mode.MOVE, timeout=1.5)
    except WaitTimedOut as exc:
        print(f"   observed: it did NOT take effect — {exc}")
    robot.stop()

    print("\n2) a bounded hold cannot run away")
    # (On the simulator a DAMP suppresses STAND, so we demonstrate the hold's bound
    #  from the current posture: the SDK sends the zero itself when time is up.)
    sent = robot.set_velocity(vx=0.2, duration=1.0)
    time.sleep(1.4)
    print(f"   hold of {sent.command} expired; SDK sent zero velocity by itself")

    print("\n3) the clamp is visible, not silent")
    sent = robot.set_velocity(vx=5.0)
    print(f"   asked 5.0 m/s -> sent {sent.command.vx} m/s, clamped={sent.clamped}")
    robot.stop()

    print("\n4) three ways to stop")
    print("   stop()  -> zero velocity, robot keeps its feet")
    robot.stop()
    print("   damp()  -> motors compliant now: the emergency verb")
    robot.damp()
    s = robot.wait_for(Mode.DAMP, timeout=5.0)
    print(f"   firmware reports {s.mode.name}")
print(
    "   close() -> ran on the with-block exit: zero velocity if one was held, then the link dropped"
)
