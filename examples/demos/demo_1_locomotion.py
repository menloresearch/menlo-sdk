"""Demo 1 — Locomotion with real completion.

The most common thing anyone does with a legged robot: stand it up, walk it, turn it,
stop. What makes this an SDK and not a script full of sleeps is that every step waits on
the ROBOT's own report — `wait_for(Mode.STAND)` returns when the firmware says it is
standing, not after a guessed delay.

    python demo_1_locomotion.py [host]       # default 127.0.0.1 (menlo-studio up --container --sdk)
"""

import sys
import time

from asimov_sdk import Mode, Robot

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
t0 = time.monotonic()


def say(msg: str) -> None:
    print(f"[{time.monotonic() - t0:5.1f}s] {msg}", flush=True)


with Robot.connect_direct(host) as robot:
    say(f"connected: {robot.info}")

    say("stand()")
    robot.stand()
    s = robot.wait_for(Mode.STAND, timeout=15.0)
    say(f"  firmware reports STAND  upright={s.upright}  gravity_z={s.gravity[2]:+.2f}")

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
    say(f"  turn over: upright={robot.state.upright}  gravity_z={robot.state.gravity[2]:+.2f}")

    say("stand()  — back to the STAND posture")
    robot.stand()
    robot.wait_for(Mode.STAND, timeout=10.0)
    say("  firmware reports STAND")

say("left the with-block: close() sent nothing extra (no velocity was held); robot stays standing")
