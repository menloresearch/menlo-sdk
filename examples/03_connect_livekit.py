"""Connect in livekit mode: control, state, camera and audio through the robot's LiveKit room.

Works wherever the robot's Asimov Manager is reachable. Needs an SDK credential in MENLO_CREDENTIAL.
Run: MENLO_CREDENTIAL=... python examples/03_connect_livekit.py http://192.168.22.32
"""

import argparse
import os

from menlo.asimov import ConnectionConfig, ManagerConfig, Robot

parser = argparse.ArgumentParser(description="Connect in livekit mode and print the robot's state.")
parser.add_argument("manager", help="the robot's Asimov Manager URL")
args = parser.parse_args()
credential = os.environ.get("MENLO_CREDENTIAL", "")
if not credential:
    parser.error("set MENLO_CREDENTIAL to an SDK credential from Asimov Manager")

# region connect
config = ConnectionConfig(livekit=ManagerConfig(url=args.manager, credential=credential))

with Robot(config).connect("livekit") as robot:
    print(robot.info.endpoint)  # room@url as the identity Asimov Manager issued
    print(f"robot mode {robot.state.mode.name}")
    print(f"camera {robot.has('camera')}, microphone {robot.has('microphone')}")
# endregion
