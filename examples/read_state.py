"""A tour of what the robot reports about itself, then a few seconds of it as a stream.

Sends nothing, so it is safe in any robot mode. Connects to the saved robot (`menlo setup`),
or to the robot the MENLO_* environment variables describe.
Run: python examples/read_state.py
"""

import time
from collections import deque

from menlo.asimov import Alert, Mode, Robot

HOTTEST = 3  # how many of the warmest actuators to list
STREAM_LINES = 10  # lines to print while streaming
STREAM_PERIOD_S = 0.5  # seconds between those lines


def on_mode_change(before: Mode, after: Mode) -> None:
    # Called on the SDK's reader thread whenever the reported robot mode changes.
    print(f"robot mode {before.name} -> {after.name}")


def on_alert(alert: Alert, change: str) -> None:
    # Called when a firmware alert appears ("raised") or goes away ("cleared").
    print(f"alert {alert.name} {change}")


with Robot().connect() as robot:
    # Note when each sample arrives, to show the state rate: samples in the last second.
    arrivals: deque[float] = deque(maxlen=5000)
    robot.on_state = lambda state: arrivals.append(state.received_at)
    robot.on_mode_change = on_mode_change
    robot.on_alert = on_alert

    # region main
    # robot.state is the latest sample the robot sent. It is a snapshot: read it again
    # for newer values. A field the robot does not report is None, never a guess.
    s = robot.state

    # Robot mode: DAMP (limp), STAND (stiff standing pose, no balance) or MOVE (the walking
    # policy balances the robot, at zero or any velocity).
    print(f"robot mode   {s.mode.name}")

    # Armed: the firmware accepts MOVE only after STAND has been held upright for 0.5 s.
    # None means the SDK cannot tell (no gravity vector reported).
    print(f"armed        {robot.armed}")

    # Faulted: a critical alert latched DAMP. The robot stays in DAMP, and error_flags stay
    # set, until the firmware restarts. The preflight check names what latched.
    print(f"faulted      {s.faulted}")
    for problem in robot.preflight("stand").problems:
        if problem.code == "faulted":
            print(f"  {problem.message}")

    # Battery, from the battery management system. None when the robot reports none.
    if s.battery is not None:
        b = s.battery
        print(
            f"battery      {b.soc_percent:.0f} %, {b.voltage_v:.1f} V, {b.current_a:+.1f} A, "
            f"warmest cell {b.max_cell_temp_c:.0f} °C, protecting {b.protecting}"
        )
    else:
        print("battery      not reported")

    # Actuators: one Joint per actuator, with the firmware's name, position (rad), velocity
    # (rad/s), current (A) and temperature (°C). The firmware warns at 60 °C and latches
    # DAMP at 80 °C.
    temps = sorted(((j.temp, j.name) for j in s.joints if j.temp is not None), reverse=True)
    if temps:
        hottest = ", ".join(f"{name} {temp:.0f} °C" for temp, name in temps[:HOTTEST])
        print(f"hottest      {hottest}")
    else:
        print("hottest      actuator temperatures not reported")

    # IMU: gravity as the body sees it. z is close to -1 when the robot is upright;
    # upright is True below -0.8. euler is (roll, pitch, yaw) in rad from the IMU quaternion.
    if s.gravity is not None:
        gx, gy, gz = s.gravity
        print(f"gravity      ({gx:+.2f}, {gy:+.2f}, {gz:+.2f}), upright {s.upright}")
    if s.euler is not None:
        roll, pitch, yaw = s.euler
        print(f"orientation  roll {roll:+.2f}, pitch {pitch:+.2f}, yaw {yaw:+.2f} rad")

    # Alerts the firmware reports now. Severity 0 is critical: it latches DAMP.
    alerts = [f"{a.name}{' (critical)' if a.critical else ''}" for a in s.alerts]
    print(f"alerts       {', '.join(alerts) or 'none'}")

    # Freshness: how old the sample is. Decisions to move are made on samples at most
    # 0.5 s old; robot.preflight() reports stale_state beyond that.
    print(f"state age    {s.age_s * 1000:.0f} ms")

    # The same fields as a stream, a few times a second.
    for _ in range(STREAM_LINES):
        time.sleep(STREAM_PERIOD_S)
        s = robot.state
        now = time.monotonic()
        rate = sum(1 for t in tuple(arrivals) if now - t <= 1.0)
        battery = f"{s.battery.soc_percent:.0f} %" if s.battery else "n/a"
        print(
            f"{s.mode.name:5}  armed {robot.armed}  upright {s.upright}  faulted {s.faulted}  "
            f"battery {battery}  age {s.age_s * 1000:.0f} ms  rate {rate:.0f} Hz"
        )
    # endregion
