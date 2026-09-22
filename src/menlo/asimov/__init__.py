"""``menlo.asimov`` — drive an Asimov robot from Python.

    from menlo.asimov import Mode, Robot

    with Robot().connect() as robot:                 # the robot from the environment or
        robot.stand()                                # ~/.menlo/robots.toml (`menlo login`)
        robot.wait_for(Mode.STAND)
        robot.set_velocity(vx=0.25, duration=4.0, wait=True)

    from menlo.asimov import ConnectionConfig, UdpConfig, ManagerConfig

    cfg = ConnectionConfig(                          # or say where the robot is
        udp=UdpConfig(host="asimov.local"),
        livekit=ManagerConfig(url="http://asimov.local", credential=CREDENTIAL),
    )
    with Robot(cfg).connect("hybrid") as robot:      # or "udp" / "livekit"
        ...

One ``Robot``, one API, three lanes chosen at connect time: ``"udp"`` (the robot's LAN
lane, no server), ``"hybrid"`` (UDP control + LiveKit media) and ``"livekit"`` (everything
over the room). LiveKit is an optional extra — the core SDK's only runtime dependency is
protobuf.
"""

from menlo import __version__
from menlo.asimov._command import Command, Limits, ModeCommand, Trajectory, Velocity
from menlo.asimov._errors import (
    CommandRefusedError,
    ConnectError,
    LinkLostError,
    MenloError,
    NotConnectedError,
    OutcomeUnknownError,
    ProtocolMismatchError,
    RobotFaultedError,
    StateStaleError,
    UnsupportedError,
    WaitTimeoutError,
)
from menlo.asimov._media import AudioChunk, Camera, Clip, Frame, Microphone, Speaker
from menlo.asimov._outcome import Applied, Outcome, Refusal, Refused, Sent, Unknown
from menlo.asimov._state import (
    Alert,
    Battery,
    BatteryProtection,
    Capability,
    Joint,
    Mode,
    RobotInfo,
    State,
)
from menlo.asimov.connection import (
    ConnectionConfig,
    ConnectMode,
    LiveKitConfig,
    ManagerConfig,
    UdpConfig,
)
from menlo.asimov.recording import Recording
from menlo.asimov.robot import Robot
from menlo.asimov.store import RobotStore, StoredRobot
from menlo.asimov.transport import HybridTransport, LiveKitTransport, Transport, UdpTransport

__all__ = [
    "Alert",
    "Applied",
    "AudioChunk",
    "Battery",
    "BatteryProtection",
    "Camera",
    "Capability",
    "Clip",
    "Command",
    "CommandRefusedError",
    "ConnectError",
    "ConnectMode",
    "ConnectionConfig",
    "Frame",
    "HybridTransport",
    "Joint",
    "Limits",
    "LinkLostError",
    "LiveKitConfig",
    "LiveKitTransport",
    "ManagerConfig",
    "MenloError",
    "Microphone",
    "Mode",
    "ModeCommand",
    "NotConnectedError",
    "Outcome",
    "OutcomeUnknownError",
    "ProtocolMismatchError",
    "Recording",
    "Refusal",
    "Refused",
    "Robot",
    "RobotFaultedError",
    "RobotInfo",
    "RobotStore",
    "Sent",
    "Speaker",
    "State",
    "StateStaleError",
    "StoredRobot",
    "Trajectory",
    "Transport",
    "UdpConfig",
    "UdpTransport",
    "Unknown",
    "UnsupportedError",
    "Velocity",
    "WaitTimeoutError",
    "__version__",
]
