"""asimov-sdk — drive an Asimov robot from Python.

    from asimov_sdk import Robot, Mode

    with Robot.connect_direct("asimov.local") as robot:
        robot.stand()
        robot.wait_for(Mode.STAND)
        robot.set_velocity(vx=0.25, duration=4.0)

One ``Robot``, one API, pluggable transports. The direct LAN lane ships today; the
cloud lane plugs into the same ``Transport`` seam later.
"""

from asimov_sdk._command import Limits, ModeCommand, Trajectory, Velocity
from asimov_sdk._errors import (
    AsimovError,
    CommandRefusedError,
    ConnectFailed,
    LinkLost,
    NotConnected,
    OutcomeUnknownError,
    ProtocolMismatch,
    RobotFaulted,
    StateStale,
    WaitTimedOut,
)
from asimov_sdk._outcome import Applied, Outcome, Refusal, Refused, Sent, Unknown
from asimov_sdk._state import Alert, Joint, Mode, RobotInfo, State
from asimov_sdk.robot import Robot
from asimov_sdk.transport import Transport, UdpTransport

__version__ = "0.1.0"

__all__ = [
    "Alert",
    "Applied",
    "AsimovError",
    "CommandRefusedError",
    "ConnectFailed",
    "Joint",
    "Limits",
    "LinkLost",
    "Mode",
    "ModeCommand",
    "NotConnected",
    "Outcome",
    "OutcomeUnknownError",
    "ProtocolMismatch",
    "Refusal",
    "Refused",
    "Robot",
    "RobotFaulted",
    "RobotInfo",
    "Sent",
    "State",
    "StateStale",
    "Trajectory",
    "Transport",
    "UdpTransport",
    "Unknown",
    "Velocity",
    "WaitTimedOut",
    "__version__",
]
