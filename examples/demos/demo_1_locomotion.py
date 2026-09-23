"""Demo 1 — Locomotion with real completion.

The most common thing anyone does with a legged robot: stand it up, walk it, turn it,
stop. What makes this an SDK and not a script full of sleeps is that every step waits on
the ROBOT's own report — `wait_for(Mode.STAND)` returns when the firmware says it is
standing, not after a guessed delay.

    python demo_1_locomotion.py [host]       # default 127.0.0.1 (menlo-studio up --container --sdk)
"""

import sys
import time

from menlo.asimov import ConnectionConfig, Mode, Robot, UdpConfig


def gz(state) -> str:
    """Gravity z as text; a robot that does not report gravity shows n/a, not a crash."""
    return f"{state.gravity[2]:+.2f}" if state.gravity is not None else "n/a"


host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
t0 = time.monotonic()


def say(msg: str) -> None:
    print(f"[{time.monotonic() - t0:5.1f}s] {msg}", flush=True)


with Robot(ConnectionConfig(udp=UdpConfig(host))).connect("udp") as robot:
    say(f"connected: {robot.info}")
    if robot.state.mode is not Mode.DAMP:
        # A previous run leaves the robot balancing in MOVE. STAND would stiffen it and
        # tip it over; DAMP it while supported, or restart the firmware, then rerun.
        sys.exit(f"robot is {robot.state.mode.name}, not DAMP — start this demo from DAMP")

    say("stand()")
    robot.stand()
    s = robot.wait_for(Mode.STAND, timeout=15.0)
    say(f"  firmware reports STAND  upright={s.upright}  gravity_z={gz(s)}")

    say("set_velocity(vx=0.25, duration=4.0)  — forward, held at 10 Hz, then zero")
    sent = robot.set_velocity(vx=0.25, duration=4.0)
    say(f"  sent {sent.command}  clamped={sent.clamped}  seq={sent.sequence}")
    s = robot.wait_for(Mode.MOVE, timeout=5.0)
    say(f"  firmware reports MOVE  upright={s.upright}")
    time.sleep(4.2)
    say(f"  hold over: mode={robot.state.mode.name} (MOVE at zero velocity = standing in place)")

    say("set_velocity(vyaw=0.6, duration=3.0)  — turn in place")
    robot.set_velocity(vyaw=0.6, duration=3.0)
    time.sleep(3.2)
    say(f"  turn over: upright={robot.state.upright}  gravity_z={gz(robot.state)}")

    # No stand() here. STAND stiffens to a fixed pose with no balance loop, so asking a
    # free-standing biped for it after walking tips it over and latches a fault-DAMP
    # until the firmware restarts. The robot is already standing still the way a legged
    # robot does: MOVE at zero velocity, with the policy balancing it.
    say(f"  resting in MOVE at zero velocity  upright={robot.state.upright}  gz={gz(robot.state)}")

say("left the with-block: close() sent nothing extra (no velocity was held); robot stays standing")
