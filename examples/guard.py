"""An optional guard on your side: stop a script when the robot reports what you refuse.

The SDK does not refuse a command because of what the robot reports. Safety is the
firmware's job (it warns at 60 C and latches DAMP at 80 C on an actuator, and warns below
20 % battery), command handling is Asimov Edge's job, and a guard is yours. This is one:
your rule, in plain Python: change the constants, or change guard(). It is an example, and
no other script uses it by default: the motion examples carry it as two commented lines,
`from guard import guard` and `guard(robot)`, to uncomment so it runs before they send
anything. On its own it prints what it finds and sends nothing.
Run: python examples/guard.py
"""

import sys

from menlo.asimov import Robot

# Your limits.
MAX_JOINT_TEMP_C = 80.0  # the firmware raises MOTOR_TEMP_HIGH at 60 C and latches DAMP at 80 C
MIN_BATTERY_PERCENT = 20.0  # the firmware raises BMS_LOW_SOC below 20 %
REFUSE_WHEN_FAULTED = True  # a latched fault holds the robot in DAMP until the firmware restarts
REFUSE_ON_ALERTS = True  # an active warning or critical alert; info alerts are printed only


# region main
def guard(robot: Robot) -> None:
    """Print what the robot reports, and exit with status 1 when your rule fails."""
    s = robot.get_state()  # one sample: every fact below comes from it
    stop: list[str] = []

    print(f"guard: robot mode {s.mode.name}, armed {robot.armed}, faulted {s.faulted}")
    if s.faulted and REFUSE_WHEN_FAULTED:
        stop.append("the firmware latched DAMP; it stays so until the firmware restarts")

    temps = [(j.temp, j.name or f"joint {i}") for i, j in enumerate(s.joints) if j.temp is not None]
    if temps:
        hottest, joint = max(temps)
        print(f"guard: hottest joint {joint} {hottest:.0f} C")
        if hottest >= MAX_JOINT_TEMP_C:
            stop.append(f"{joint} is at {hottest:.0f} C (your limit {MAX_JOINT_TEMP_C:.0f} C)")
    else:
        print("guard: joint temperatures not reported")

    if s.battery is not None:
        print(f"guard: battery {s.battery.soc_percent:.0f} %")
        if s.battery.soc_percent < MIN_BATTERY_PERCENT:
            stop.append(
                f"battery at {s.battery.soc_percent:.0f} % (your limit {MIN_BATTERY_PERCENT:.0f} %)"
            )
        if s.battery.protecting:
            stop.append("the battery management system is protecting the pack")
    else:
        print("guard: battery not reported")

    for alert in s.alerts:
        print(f"guard: alert {alert.name} (severity {alert.severity})")
    serious = sorted({a.name for a in s.alerts if a.severity <= 1})  # 0 critical, 1 warning
    if serious and REFUSE_ON_ALERTS:
        stop.append(f"active alerts: {', '.join(serious)}")

    if stop:
        print("guard: not going ahead: " + "; ".join(stop))
        print("guard: this is your rule in guard.py; change it, or delete the guard(robot) line")
        sys.exit(1)


# endregion

if __name__ == "__main__":
    with Robot().connect() as robot:
        guard(robot)
        print("guard: nothing stops this script")
