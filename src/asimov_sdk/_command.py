"""What the SDK sends, in the robot's own vocabulary.

The verbs on :class:`~asimov_sdk.robot.Robot` are the wire's verbs — ``set_velocity``,
``stand``, ``damp``, ``stop``, ``trajectory`` — because that is what the edge, the
protocol and the robot's other controllers already call them. These dataclasses are
their payloads, transport-neutral: the UDP transport encodes them as
``asimov.io.RobotCommand``; a future cloud transport will encode the same objects as
``menlo.edge.CloudCommand``. Nothing above the transport knows which.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Limits:
    """The clamp the SDK applies before anything reaches the robot.

    The edge and firmware clamp too; this one exists so a typo'd ``vx=20`` in a notebook is
    a visibly clamped command rather than a lunge. Strafe shares the forward ceiling: it is
    the same walking gait, not a separate faster mode.
    """

    vx: float = 0.6  # m/s
    vy: float = 0.6  # m/s
    vyaw: float = 1.5  # rad/s

    def __post_init__(self) -> None:
        for name in ("vx", "vy", "vyaw"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                # A negative limit would turn a requested stop into motion (clamp of 0
                # into [-l, l] with l < 0 is -l). A limit is a magnitude, or nothing.
                raise ValueError(f"Limits.{name} must be finite and >= 0, got {value!r}")


@dataclass(frozen=True, slots=True)
class Velocity:
    """A POLICY-mode body velocity. Units are the firmware's: m/s, m/s, rad/s."""

    vx: float = 0.0  # forward, m/s
    vy: float = 0.0  # left, m/s
    vyaw: float = 0.0  # counter-clockwise, rad/s

    def __post_init__(self) -> None:
        for name in ("vx", "vy", "vyaw"):
            v = getattr(self, name)
            if not isinstance(v, int | float) or isinstance(v, bool) or not math.isfinite(v):
                raise ValueError(f"{name} must be a finite number, got {v!r}")

    def clamped(self, limits: Limits) -> Velocity:
        return Velocity(
            max(-limits.vx, min(limits.vx, float(self.vx))),
            max(-limits.vy, min(limits.vy, float(self.vy))),
            max(-limits.vyaw, min(limits.vyaw, float(self.vyaw))),
        )

    @property
    def is_zero(self) -> bool:
        return self.vx == 0.0 and self.vy == 0.0 and self.vyaw == 0.0


ModeName = Literal["stand", "damp"]


@dataclass(frozen=True, slots=True)
class ModeCommand:
    """A posture: STAND or DAMP. An event, not a setpoint — sent once, never latched."""

    mode: ModeName

    def __post_init__(self) -> None:
        if self.mode not in ("stand", "damp"):
            raise ValueError(f"mode must be 'stand' or 'damp', got {self.mode!r}")


@dataclass(frozen=True, slots=True)
class Trajectory:
    """Direct joint targets for EVERY motor, in firmware order, radians.

    A setpoint, not a queue: the robot holds the latest one. Length must equal the
    robot's DOF; the edge refuses anything else, so we refuse first. ``kp``/``kd`` are
    optional per-joint gains; ``None`` means the firmware's defaults.
    """

    positions: tuple[float, ...]
    kp: tuple[float, ...] | None = None
    kd: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not self.positions:
            raise ValueError("trajectory needs at least one position")
        for name in ("positions", "kp", "kd"):
            seq = getattr(self, name)
            if seq is None:
                continue
            if any(not math.isfinite(x) for x in seq):
                raise ValueError(f"{name} contains a non-finite value")
            if name != "positions" and len(seq) != len(self.positions):
                raise ValueError(f"{name} has {len(seq)} values for {len(self.positions)} joints")


Command = Velocity | ModeCommand | Trajectory
