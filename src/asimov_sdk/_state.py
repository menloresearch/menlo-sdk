"""What the robot reports, typed.

A :class:`State` is one telemetry sample, normalised so that both transports produce
the same object: the UDP lane carries ``asimov.io.RobotState``, the cloud lane will
carry ``menlo.edge.EdgeTelemetry``, and neither name leaks above the transport.

Fields the robot cannot report are ``None``, never a plausible default. There is
deliberately no ``pose``: the firmware has no odometry and neither lane carries one.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Literal

from asimov_sdk._command import Limits

Transport = Literal["direct", "cloud"]


class Mode(enum.IntEnum):
    """The firmware's posture, as REPORTED. Values are ``asimov.io.ControlMode``.

    Never passed to a command: the verbs ``stand()``, ``damp()`` and ``set_velocity()``
    imply the mode they need.
    """

    DAMP = 0
    STAND = 1
    MOVE = 2
    UNKNOWN = -1

    @classmethod
    def from_wire(cls, value: int) -> Mode:
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class Alert:
    """One firmware alert. ``severity`` is the firmware's scale: 0 is CRITICAL."""

    id: int
    severity: int
    value: float
    threshold: float
    source_id: int

    @property
    def critical(self) -> bool:
        return self.severity == 0


@dataclass(frozen=True, slots=True)
class Joint:
    name: str  # firmware name, or "" when the SDK has no table for this robot
    pos: float  # rad
    vel: float  # rad/s
    current: float | None  # A
    temp: float | None  # °C


@dataclass(frozen=True, slots=True)
class State:
    """One sample of the robot's own report."""

    mode: Mode
    joints: tuple[Joint, ...]
    gravity: tuple[float, float, float] | None  # projected gravity, body frame; z ≈ -1 upright
    gyro: tuple[float, float, float] | None  # rad/s
    quat: tuple[float, float, float, float] | None  # w, x, y, z
    error_flags: int
    alerts: tuple[Alert, ...]
    sequence: int
    fw_timestamp_us: int
    protocol_version: int
    received_at: float = field(default_factory=time.monotonic)

    @property
    def age_s(self) -> float:
        """Seconds since this sample arrived. A wait treats a large value as absence."""
        return time.monotonic() - self.received_at

    @property
    def upright(self) -> bool | None:
        """On its feet, by measured gravity. ``None`` when the robot did not report it."""
        if self.gravity is None:
            return None
        return self.gravity[2] < -0.8

    @property
    def faulted(self) -> bool:
        """The firmware's own fault test: an error flag, or any critical alert. The shipped
        firmware never writes ``error_flags``, so the alerts are the half that matters."""
        return bool(self.error_flags) or any(a.critical for a in self.alerts)

    @property
    def joint_pos(self) -> tuple[float, ...]:
        return tuple(j.pos for j in self.joints)

    def joint(self, name: str) -> Joint:
        """Look a joint up by firmware name. Raises ``KeyError`` on a typo — silently
        indexing the wrong joint is the failure this exists to prevent."""
        for j in self.joints:
            if j.name == name:
                return j
        known = ", ".join(j.name for j in self.joints if j.name) or "(no names known)"
        raise KeyError(f"no joint named {name!r}; this robot has: {known}")


@dataclass(frozen=True, slots=True)
class RobotInfo:
    """What the SDK can say about the body it is attached to. Read once at connect.

    Discovery is not on the wire yet, so everything here is derived from the transport
    and the first state sample. Fields that need the robot to describe itself are
    ``None`` rather than guessed.
    """

    transport: Transport
    endpoint: str
    dof: int
    joint_names: tuple[str, ...] | None
    protocol_version: int
    limits: Limits
    model: str | None = None
    robot_id: str | None = None

    def joint_index(self, name: str) -> int:
        if self.joint_names is None:
            raise KeyError("this robot's joint names are not known to the SDK")
        try:
            return self.joint_names.index(name)
        except ValueError:
            raise KeyError(
                f"no joint named {name!r}; known: {', '.join(self.joint_names)}"
            ) from None

    def __str__(self) -> str:
        model = self.model or "asimov"
        return (
            f"{model} via {self.transport} ({self.endpoint}) "
            f"dof={self.dof} proto=v{self.protocol_version}"
        )
