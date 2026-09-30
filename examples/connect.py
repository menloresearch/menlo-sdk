"""Connect in one of the three connection modes and print what the robot reports. Sends nothing.

Set MODE and the settings that mode needs. udp needs the robot's address. hybrid needs the
robot's address, the Asimov Manager URL and an SDK credential. livekit needs the Asimov
Manager URL and an SDK credential. The SDK credential comes from the Developer page of
Asimov Manager; this script reads it from MENLO_CREDENTIAL so it stays out of the file.
Run: python examples/connect.py
"""

import os

from menlo.asimov import ConnectionConfig, ConnectMode, ManagerConfig, Robot, UdpConfig

MODE: ConnectMode = "udp"  # "udp", "hybrid" or "livekit"
ROBOT_ADDRESS = "192.168.22.32"  # udp and hybrid
MANAGER_URL = "http://192.168.22.32"  # hybrid and livekit
CREDENTIAL = os.environ.get("MENLO_CREDENTIAL", "")  # hybrid and livekit

# region main
# udp: control and state over UDP on the robot's network. No camera or audio.
udp = ConnectionConfig(udp=UdpConfig(host=ROBOT_ADDRESS))

# hybrid: control and state over UDP, camera and audio over LiveKit.
hybrid = ConnectionConfig(
    udp=UdpConfig(host=ROBOT_ADDRESS),
    livekit=ManagerConfig(url=MANAGER_URL, credential=CREDENTIAL),
)

# livekit: everything through the robot's LiveKit room, wherever Asimov Manager is reachable.
livekit = ConnectionConfig(livekit=ManagerConfig(url=MANAGER_URL, credential=CREDENTIAL))

config = {"udp": udp, "hybrid": hybrid, "livekit": livekit}[MODE]

with Robot(config).connect(MODE) as robot:
    state = robot.state
    print(f"connected over {MODE} to {robot.info.endpoint}")
    print(f"robot mode {state.mode.name}, armed {robot.armed}")
    if state.battery is not None:
        print(f"battery {state.battery.soc_percent:.0f} %")
    else:
        print("battery not reported")
    capabilities = ("drive", "state", "battery", "camera", "microphone", "speaker")
    print("capabilities:", ", ".join(c for c in capabilities if robot.has(c)))
# endregion
