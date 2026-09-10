"""asimov-sdk — drive an Asimov robot from Python.

    from asimov_sdk import Robot, Mode

    with Robot.connect("asimov.local") as robot:
        robot.stand()
        robot.wait_for(Mode.STAND)
        robot.set_velocity(vx=0.25, duration=4.0)

One ``Robot``, one API, pluggable transports: ``UdpTransport`` speaks the robot's LAN
lane; any other wire implements ``asimov_sdk.Transport``.
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
from asimov_sdk._media import AudioChunk, Camera, Frame, Microphone, Speaker
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
from asimov_sdk.transport import Transport, UdpTransport

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
    "Command",
    "CommandRefusedError",
    "ConnectError",
    "Frame",
    "Joint",
    "Limits",
    "LinkLostError",
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
