"""asimov-sdk — drive an Asimov robot from Python.

    from asimov_sdk import Robot, Mode

    with Robot.connect("asimov.local") as robot:
        robot.stand()
        robot.wait_for(Mode.STAND)
        robot.set_velocity(vx=0.25, duration=4.0)

One ``Robot``, one API, pluggable transports. Three lanes ship: ``UdpTransport`` (the
robot's LAN lane, no server), ``HybridTransport`` (UDP control + LiveKit media) and
``LiveKitTransport`` (everything over the room). LiveKit is an optional extra — the core
SDK's only runtime dependency is protobuf.
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
    "Frame",
    "HybridTransport",
    "Joint",
    "Limits",
    "LinkLostError",
    "LiveKitTransport",
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
    "UdpTransport",
    "Unknown",
    "UnsupportedError",
    "Velocity",
    "WaitTimeoutError",
    "__version__",
]
