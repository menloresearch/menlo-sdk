"""``menlo.asimov``: drive an Asimov robot from Python.

    from menlo.asimov import Robot

    with Robot().connect() as robot:                 # the saved robot (`menlo setup`)
        robot.stand()                                # checks first; returns once armed
        robot.balance()                              # MOVE: balancing in place
        robot.set_velocity(vx=0.25, duration=4.0)    # walk 4 s, then zero velocity
        robot.balance()                              # stays in MOVE, balancing

    from menlo.asimov import ConnectionConfig, UdpConfig, ManagerConfig

    cfg = ConnectionConfig(                          # or say where the robot is
        udp=UdpConfig(host="192.168.22.32"),
        livekit=ManagerConfig(url="http://192.168.22.32", credential=CREDENTIAL),
    )
    with Robot(cfg).connect("hybrid") as robot:      # or "udp" / "livekit"
        ...

One ``Robot``, one API, three connection modes chosen at connect time: ``"udp"`` (control
and state over UDP on the robot's network, no media), ``"hybrid"`` (control and state over
UDP, camera and audio over LiveKit) and ``"livekit"`` (everything through the robot's
LiveKit room). ``livekit`` is imported only when a room is joined.
"""

from menlo import __version__
from menlo.asimov._command import Command, Limits, ModeCommand, Trajectory, Velocity
from menlo.asimov._errors import (
    CommandRefusedError,
    ConnectError,
    LinkLostError,
    MenloError,
    NotConnectedError,
    NotReadyError,
    OutcomeUnknownError,
    ProtocolMismatchError,
    RobotFaultedError,
    StateStaleError,
    UnsupportedError,
    WaitTimeoutError,
)
from menlo.asimov._media import AudioChunk, Camera, Clip, Frame, Microphone, Speaker
from menlo.asimov._outcome import Applied, Outcome, Refusal, Refused, Sent, Unknown
from menlo.asimov._preflight import Action, Preflight, Problem
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
    ManagerGrant,
    UdpConfig,
)
from menlo.asimov.recording import Recording
from menlo.asimov.robot import Robot
from menlo.asimov.store import RobotStore, StoredRobot
from menlo.asimov.transport import HybridTransport, LiveKitTransport, Transport, UdpTransport

__all__ = [
    "Action",
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
    "ManagerGrant",
    "MenloError",
    "Microphone",
    "Mode",
    "ModeCommand",
    "NotConnectedError",
    "NotReadyError",
    "Outcome",
    "OutcomeUnknownError",
    "Preflight",
    "Problem",
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
