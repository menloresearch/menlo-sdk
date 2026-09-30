"""Print the robot's state twice a second for 5 s, and every robot mode change and alert.

Sends nothing. Connection mode: the saved robot's. Run: python examples/07_read_state.py
"""

import time

from menlo.asimov import Alert, Mode, Robot


def on_mode_change(before: Mode, after: Mode) -> None:
    print(f"robot mode {before.name} -> {after.name}")


def on_alert(alert: Alert, change: str) -> None:
    print(f"alert {alert.name} {change}")


with Robot().connect() as robot:
    robot.on_mode_change = on_mode_change
    robot.on_alert = on_alert

    # region read
    for _ in range(10):
        s = robot.state
        temps = [(j.temp, j.name) for j in s.joints if j.temp is not None]
        hottest = "{1} {0:.0f} C".format(*max(temps)) if temps else "not reported"
        battery = f"{s.battery.soc_percent:.0f} %" if s.battery else "not reported"
        print(
            f"{s.mode.name:5}  armed {robot.armed}  upright {s.upright}  "
            f"faulted {s.faulted}  battery {battery}  hottest joint {hottest}  "
            f"age {s.age_s * 1000:.0f} ms"
        )
        time.sleep(0.5)
    # endregion
