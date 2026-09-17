"""Demo 2 — Typed telemetry, and noticing when it stops.

Reading the robot is the other half of every SDK. Here the state is a typed sample:
joints by firmware NAME, projected gravity, alerts with the firmware's severity scale,
and — the part most SDKs skip — how OLD the sample is. A monitor that cannot tell a
frozen robot from a still one is not a monitor.

    python demo_2_telemetry.py [host] [seconds]
"""

import sys
import time

from asimov_sdk import ConnectionConfig, Mode, Robot, StateStaleError, UdpConfig


def gz(state) -> str:
    """Gravity z as text; a robot that does not report gravity shows n/a, not a crash."""
    return f"{state.gravity[2]:+.2f}" if state.gravity is not None else "n/a"


host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

with Robot(ConnectionConfig(udp=UdpConfig(host))).connect("udp") as robot:
    info = robot.info
    print(f"{info.dof} joints, protocol v{info.protocol_version}, via {info.transport}")
    print(f"endpoint: {info.endpoint}")
    print(f"limits: {info.limits.vx} m/s, {info.limits.vyaw} rad/s\n")

    # A gentle motion so there is something to watch.
    robot.stand()
    robot.wait_for(Mode.STAND, timeout=15.0)
    robot.set_velocity(vx=0.15, duration=seconds)

    header = f"{'t':>5}  {'mode':<5} {'up':<5} {'grav_z':>7} {'L_Knee':>8} {'R_Knee':>8}"
    print(f"{header} {'hottest':>14} {'alerts':>6} {'age':>7}")
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        s = robot.state
        hottest = max(s.joints, key=lambda j: j.temp or 0.0)
        knees = f"{s.joint('L_Knee').pos:+8.3f} {s.joint('R_Knee').pos:+8.3f}"
        hot = f"{hottest.name:>10}{(hottest.temp or 0):4.0f}"
        print(
            f"{time.monotonic() - t0:5.1f}  {s.mode.name:<5} {s.upright!s:<5} {gz(s):>7} "
            f"{knees} {hot} {len(s.alerts):>6} {s.age_s * 1000:5.1f}ms",
            flush=True,
        )
        time.sleep(0.5)

    # The completion primitive, with the staleness guard explicit.
    try:
        s = robot.wait_until(
            lambda st: st.mode is Mode.MOVE and st.upright, timeout=2.0, stale_after=1.0
        )
        print(f"\nstill upright and in MOVE after the run; sample age {s.age_s * 1000:.1f} ms")
    except StateStaleError as exc:
        print(f"\nthe robot stopped reporting: {exc}")

    critical = [a for a in robot.state.alerts if a.critical]
    print(f"faulted={robot.state.faulted}  critical alerts={[a.id for a in critical]}")
