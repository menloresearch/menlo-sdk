"""Connect in hybrid mode: control and state over UDP, camera and audio over LiveKit.

Needs the robot's address, its Asimov Manager URL and an SDK credential in MENLO_CREDENTIAL.
Run: MENLO_CREDENTIAL=... python examples/02_connect_hybrid.py 192.168.22.32 http://192.168.22.32
"""

import argparse
import os

from menlo.asimov import ConnectionConfig, ManagerConfig, Robot, UdpConfig

parser = argparse.ArgumentParser(description="Connect in hybrid mode and print what it carries.")
parser.add_argument("host", help="the robot's address")
parser.add_argument("manager", help="the robot's Asimov Manager URL")
args = parser.parse_args()
credential = os.environ.get("MENLO_CREDENTIAL", "")
if not credential:
    parser.error("set MENLO_CREDENTIAL to an SDK credential from Asimov Manager")

# region connect
config = ConnectionConfig(
    udp=UdpConfig(host=args.host),
    livekit=ManagerConfig(url=args.manager, credential=credential),
)

with Robot(config).connect("hybrid") as robot:
    print(robot.info.endpoint)
    print(f"camera {robot.has('camera')}, microphone {robot.has('microphone')}")
    print(f"robot mode {robot.state.mode.name}")
# endregion
