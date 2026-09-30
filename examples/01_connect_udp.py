"""Connect over udp and print what the robot reports. Sends nothing.

Connection mode: udp. Asimov Edge needs udp-control on and udp-state-host set to this machine.
Run: python examples/01_connect_udp.py 192.168.22.32
"""

import argparse

from menlo.asimov import ConnectionConfig, Robot, UdpConfig

parser = argparse.ArgumentParser(description="Connect over udp and print the robot's state.")
parser.add_argument("host", help="the robot's address")
args = parser.parse_args()

# region connect
config = ConnectionConfig(udp=UdpConfig(host=args.host))

with Robot(config).connect("udp") as robot:
    state = robot.state
    print(robot.info)
    print(f"robot mode {state.mode.name}, armed {robot.armed}")
    if state.battery is not None:
        print(f"battery {state.battery.soc_percent:.0f} %")
    print(f"latest state is {state.age_s * 1000:.0f} ms old")
# endregion
