"""asimov-sdk — drive an Asimov robot from Python.

    from asimov_sdk import ConnectionConfig, UdpConfig, ManagerConfig, Robot, Mode

    cfg = ConnectionConfig(
        udp=UdpConfig(host="asimov.local"),
        livekit=ManagerConfig(url="http://asimov.local:8080", credential=CREDENTIAL),
    )
    with Robot(cfg).connect("hybrid") as robot:      # or "udp" / "livekit"
        robot.stand()
        robot.wait_for(Mode.STAND)
        robot.set_velocity(vx=0.25, duration=4.0)

One ``Robot``, one API, three lanes chosen at connect time: ``"udp"`` (the robot's LAN
lane, no server), ``"hybrid"`` (UDP control + LiveKit media) and ``"livekit"`` (everything
over the room). LiveKit is an optional extra — the core SDK's only runtime dependency is
protobuf.
"""

from asimov_sdk._command import Command, Limits, ModeCommand, Trajectory, Velocity
from asimov_sdk._errors import (
    AsimovError,
    CommandRefusedError,
    ConnectError,
    LinkLostError,
    NotConnectedError,
    OutcomeUnknownError,
    ProtocolMismatchError,
    RobotFaultedError,
    StateStaleError,
    UnsupportedError,
    WaitTimeoutError,
)
from asimov_sdk._media import AudioChunk, Camera, Clip, Frame, Microphone, Speaker
from asimov_sdk._outcome import Applied, Outcome, Refusal, Refused, Sent, Unknown
from asimov_sdk._state import (
    Alert,
    Battery,
    BatteryProtection,
    Capability,
    Joint,
    Mode,
    RobotInfo,
    State,
)
from asimov_sdk.connection import (
    ConnectionConfig,
    ConnectMode,
    LiveKitConfig,
    ManagerConfig,
    UdpConfig,
)
from asimov_sdk.recording import Recording
from asimov_sdk.robot import Robot
from asimov_sdk.transport import HybridTransport, LiveKitTransport, Transport, UdpTransport

__version__ = "0.1.0"

__all__ = [
    "Alert",
    "Applied",
    "AsimovError",
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
    "Sent",
    "Speaker",
    "State",
    "StateStaleError",
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
