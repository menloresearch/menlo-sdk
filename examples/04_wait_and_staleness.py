"""`wait_until` is the completion primitive — and it notices when the robot goes quiet.

A wait that keeps checking a frozen snapshot would happily "succeed" on a dead robot.
`wait_until` raises `StateStaleError` instead once the stream is older than `stale_after`.
"""

import sys

from asimov_sdk import (
    ConnectionConfig,
    Mode,
    Robot,
    RobotFaultedError,
    StateStaleError,
    UdpConfig,
    WaitTimeoutError,
)

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with Robot(ConnectionConfig(udp=UdpConfig(host))).connect("udp") as robot:
    robot.stand()
    try:
        s = robot.wait_until(
            lambda st: st.mode is Mode.STAND and st.upright is True,
            timeout=15.0,
            stale_after=2.0,
        )
        print(f"settled: mode={s.mode.name} upright={s.upright} sample_age={s.age_s:.3f}s")
    except RobotFaultedError as exc:
        print(f"the firmware fault-DAMPed: {exc}")  # retrying will not help; clear the fault
    except StateStaleError as exc:
        print(f"the robot stopped talking: {exc}")  # treat as absent, not slow
    except WaitTimeoutError as exc:
        print(f"still not there: {exc}")

    # Joint temperatures, by name, from the same typed sample.
    hot = max(robot.state.joints, key=lambda j: j.temp or 0.0)
    print(f"hottest joint: {hot.name} {hot.temp} °C")
