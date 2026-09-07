"""What the robot reports, typed.

A :class:`State` is one telemetry sample, normalised so that both transports produce
the same object: the UDP lane carries ``asimov.io.RobotState``, the cloud lane will
carry ``menlo.edge.EdgeTelemetry``, and neither name leaks above the transport.

Fields the robot cannot report are ``None``, never a plausible default. There is
deliberately no ``pose``: the firmware has no odometry and neither lane carries one.
"""

from __future__ import annotations

import enum
import math
import time
from dataclasses import dataclass, field
from typing import Literal

from asimov_sdk._command import Limits

TransportKind = str  # a transport names its wire: UdpTransport says "direct"

#: What a robot, over a given transport, can do for a script. ``drive`` and ``state`` are
#: what every transport must carry; the rest depend on the robot and the wire.
Capability = Literal["drive", "state", "battery", "camera", "microphone", "speaker"]


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
    first_set_us: int = 0  # firmware clock when the alert was raised; 0 when not reported

    @property
    def critical(self) -> bool:
        return self.severity == 0

    @property
    def name(self) -> str:
        """The firmware's name for this alert id, or ``"ALERT_<id>"`` when the SDK has none."""
        return ALERT_NAMES.get(self.id, f"ALERT_{self.id}")


#: Alert ids as named in the firmware's ``lib/alert_types.h``. Severity is 0 (critical)
#: for ids 0-15, 1 (warning) for 16-31, 2 (info) for 32-47.
ALERT_NAMES: dict[int, str] = {
    0: "MOTOR_COMM_LOSS",
    1: "MOTOR_OVERCURRENT",
    2: "MOTOR_OVERTEMP",
    3: "IMU_FAILURE",
    4: "WATCHDOG_TIMEOUT",
    5: "MOTOR_ENCODER_ERROR",
    7: "FALL_DETECTED",
    8: "POLICY_SPAZ",
    9: "TUMBLE_DETECTED",
    10: "MOTOR_PARTIAL_LOSS",
    11: "JOINT_OUT_OF_RANGE",
    12: "BMS_COMM_LOSS",
    13: "BMS_PROTECTION",
    16: "MOTOR_DRV_FAULT",
    17: "MOTOR_TEMP_HIGH",
    18: "IMU_DEGRADED",
    19: "CAN_ERRORS",
    20: "POLICY_LATE",
    21: "CAN_TIMEOUT",
    22: "CAN_BUS_OFF",
    23: "CAN_TX_FAILURE",
    24: "MOTOR_UNDERVOLTAGE",
    25: "MOTOR_BRAKE_OVERVOLT",
    26: "CAN_UTILIZATION_HIGH",
    27: "BMS_LOW_SOC",
    28: "INVALID_GAINS",
    32: "MODE_CHANGE",
    34: "IMU_STALE",
    35: "IMU_CALIBRATION_LOW",
    36: "POLICY_INFERENCE_TIMEOUT",
    37: "POLICY_DEADLINE_MISS",
    38: "MOTOR_MISSING",
    39: "INFERENCE_JITTER",
    40: "IMU_RATE_DROP",
    41: "BMS_CELL_TEMP_HIGH",
}


class BatteryProtection(enum.IntFlag):
    """BMS protection bits, as the firmware defines them (``bms_driver.h`` ``BMS_PROT_*``)."""

    CELL_OVERVOLT = 1 << 0
    CELL_UNDERVOLT = 1 << 1
    PACK_OVERVOLT = 1 << 2
    PACK_UNDERVOLT = 1 << 3
    CHARGE_OVERTEMP = 1 << 4
    CHARGE_LOWTEMP = 1 << 5
    DISCHARGE_OVERTEMP = 1 << 6
    DISCHARGE_LOWTEMP = 1 << 7
    CHARGE_OVERCURRENT = 1 << 8
    DISCHARGE_OVERCURRENT = 1 << 9
    SHORT_CIRCUIT = 1 << 10
    FRONTEND_IC_ERROR = 1 << 11
    SOFTWARE_MOS_LOCK = 1 << 12


@dataclass(frozen=True, slots=True)
class Battery:
    """Pack summary from the robot's battery management system."""

    voltage_v: float
    current_a: float  # positive charging, negative discharging
    soc_percent: float  # 0-100
    max_cell_temp_c: float
    protection: BatteryProtection  # zero when the BMS is not protecting

    @property
    def protecting(self) -> bool:
        return self.protection != 0

    @property
    def charging(self) -> bool:
        return self.current_a > 0.0


@dataclass(frozen=True, slots=True)
class Joint:
    name: str  # firmware name, or "" when the SDK has no table for this robot
    pos: float  # rad
    vel: float | None  # rad/s; None when the robot did not report it (never guessed)
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
    battery: Battery | None = None  # None when the robot has no BMS or did not report one
    received_at: float = field(default_factory=time.monotonic)

    @property
    def age_s(self) -> float:
        """Seconds since this sample arrived. A wait treats a large value as absence."""
        return time.monotonic() - self.received_at

    @property
    def euler(self) -> tuple[float, float, float] | None:
        """(roll, pitch, yaw) in radians from ``quat`` (w, x, y, z); ``None`` without a quat."""
        if self.quat is None:
            return None
        w, x, y, z = self.quat
        roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        return (roll, pitch, yaw)

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

    transport: TransportKind
    endpoint: str
    dof: int
    joint_names: tuple[str, ...] | None
    protocol_version: int
    limits: Limits
    capabilities: frozenset[str] = frozenset({"drive", "state"})

    def has(self, capability: Capability | str) -> bool:
        return capability in self.capabilities

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
        caps = ",".join(sorted(self.capabilities))
        return (
            f"asimov via {self.transport} ({self.endpoint}) "
            f"dof={self.dof} proto=v{self.protocol_version} caps={caps}"
        )
