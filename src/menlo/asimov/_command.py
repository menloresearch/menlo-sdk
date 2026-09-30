"""What the SDK sends, in the robot's own vocabulary.

The verbs on :class:`~menlo.asimov.robot.Robot` follow the wire (``stand``, ``damp``,
``set_velocity``, ``trajectory``; ``balance`` is a zero velocity) because that is what
Asimov Edge, the protocol and the robot's other controllers already call them. These dataclasses are
their payloads, transport-neutral: the UDP transport encodes them as
``asimov.io.RobotCommand``; another transport encodes the same objects for its own wire.
Nothing above the transport knows which.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

#: The Motion Control Board firmware clamps every velocity it receives to these magnitudes.
FIRMWARE_MAX_VX = 0.4  # m/s
FIRMWARE_MAX_VY = 0.4  # m/s
FIRMWARE_MAX_VYAW = 0.8  # rad/s


@dataclass(frozen=True, slots=True)
class Limits:
    """The clamp the SDK applies to a velocity before it is sent.

    The defaults are the firmware's own caps (0.4 m/s forward and sideways, 0.8 rad/s
    turning), so ``Sent.clamped`` is true exactly when the robot would not walk at the
    speed asked for. Lower values make a script slower than the robot allows. Higher values
    are accepted and sent as asked; the firmware then clamps them to its caps.

    Set per robot with ``Robot(limits=Limits(...))``, ``MENLO_LIMITS="vx,vy,vyaw"``, or a
    ``[robots.NAME.limits]`` table in the saved robots file.
    """

    vx: float = FIRMWARE_MAX_VX  # m/s
    vy: float = FIRMWARE_MAX_VY  # m/s
    vyaw: float = FIRMWARE_MAX_VYAW  # rad/s

    def __post_init__(self) -> None:
        for name in ("vx", "vy", "vyaw"):
            value = getattr(self, name)
            if (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                # A negative limit would turn a requested stop into motion (clamp of 0
                # into [-l, l] with l < 0 is -l). A limit is a magnitude, or nothing.
                raise ValueError(f"Limits.{name} must be finite and >= 0, got {value!r}")

    @classmethod
    def parse(cls, text: str) -> Limits:
        """``"vx,vy,vyaw"`` (the ``MENLO_LIMITS`` form), e.g. ``"0.3,0.2,0.6"``."""
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            raise ValueError(f"limits must be 'vx,vy,vyaw' (three numbers), got {text!r}")
        try:
            vx, vy, vyaw = (float(p) for p in parts)
        except ValueError:
            raise ValueError(f"limits must be 'vx,vy,vyaw' (three numbers), got {text!r}") from None
        return cls(vx, vy, vyaw)


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
    """A posture: STAND or DAMP. An event, not a setpoint: sent once, never latched."""

    mode: ModeName

    def __post_init__(self) -> None:
        if self.mode not in ("stand", "damp"):
            raise ValueError(f"mode must be 'stand' or 'damp', got {self.mode!r}")


@dataclass(frozen=True, slots=True)
class Trajectory:
    """Direct joint targets for EVERY motor, in firmware order, radians, in the same frame
    ``State.joint_pos`` reports. (On the biped, the ankle entries are the A and B motors;
    the SDK sends them as the ankle pitch and roll the firmware reads there, and the
    firmware limits those to 0.35 rad and 0.1 rad.) Holding a reported pose within those
    ankle limits holds the robot still; ``Robot.trajectory`` and ``Robot.set_joints`` refuse a
    target outside them with ``ValueError``.

    A setpoint, not a queue: the robot holds the latest one. Length must equal the
    robot's DOF; Asimov Edge refuses anything else, so we refuse first. ``kp``/``kd`` are
    optional per-joint gains; with ``None`` Asimov Edge applies its own per-joint gain table.
    """

    positions: tuple[float, ...]
    kp: tuple[float, ...] | None = None
    kd: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if (self.kp is None) != (self.kd is None):
            # Asimov Edge uses its own gain table for BOTH unless both are given; sending one
            # would be silently ignored, so refuse it here.
            raise ValueError("give both kp and kd, or neither (exactly one of kp/kd was given)")
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
