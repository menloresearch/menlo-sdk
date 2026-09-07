from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Mode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_STAND: _ClassVar[Mode]
    MODE_DAMP: _ClassVar[Mode]

class FirmwareMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FW_MODE_DAMP: _ClassVar[FirmwareMode]
    FW_MODE_STAND: _ClassVar[FirmwareMode]
    FW_MODE_MOVE: _ClassVar[FirmwareMode]

class Subsystem(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SUBSYSTEM_FW_LINK: _ClassVar[Subsystem]
    SUBSYSTEM_CAMERA: _ClassVar[Subsystem]
    SUBSYSTEM_MIC: _ClassVar[Subsystem]
    SUBSYSTEM_SPEAKER: _ClassVar[Subsystem]
    SUBSYSTEM_BLE: _ClassVar[Subsystem]
    SUBSYSTEM_CLOUD: _ClassVar[Subsystem]
    SUBSYSTEM_FIRMWARE: _ClassVar[Subsystem]
MODE_STAND: Mode
MODE_DAMP: Mode
FW_MODE_DAMP: FirmwareMode
FW_MODE_STAND: FirmwareMode
FW_MODE_MOVE: FirmwareMode
SUBSYSTEM_FW_LINK: Subsystem
SUBSYSTEM_CAMERA: Subsystem
SUBSYSTEM_MIC: Subsystem
SUBSYSTEM_SPEAKER: Subsystem
SUBSYSTEM_BLE: Subsystem
SUBSYSTEM_CLOUD: Subsystem
SUBSYSTEM_FIRMWARE: Subsystem

class CloudCommand(_message.Message):
    __slots__ = ("timestamp_us", "sequence", "velocity", "trajectory", "mode")
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    VELOCITY_FIELD_NUMBER: _ClassVar[int]
    TRAJECTORY_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    timestamp_us: int
    sequence: int
    velocity: VelocityCommand
    trajectory: TrajectoryRequest
    mode: ModeCommand
    def __init__(self, timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., velocity: _Optional[_Union[VelocityCommand, _Mapping]] = ..., trajectory: _Optional[_Union[TrajectoryRequest, _Mapping]] = ..., mode: _Optional[_Union[ModeCommand, _Mapping]] = ...) -> None: ...

class VelocityCommand(_message.Message):
    __slots__ = ("vx", "vy", "vyaw")
    VX_FIELD_NUMBER: _ClassVar[int]
    VY_FIELD_NUMBER: _ClassVar[int]
    VYAW_FIELD_NUMBER: _ClassVar[int]
    vx: float
    vy: float
    vyaw: float
    def __init__(self, vx: _Optional[float] = ..., vy: _Optional[float] = ..., vyaw: _Optional[float] = ...) -> None: ...

class TrajectoryRequest(_message.Message):
    __slots__ = ("id", "full", "upper_only")
    ID_FIELD_NUMBER: _ClassVar[int]
    FULL_FIELD_NUMBER: _ClassVar[int]
    UPPER_ONLY_FIELD_NUMBER: _ClassVar[int]
    id: str
    full: FullTrajectory
    upper_only: bool
    def __init__(self, id: _Optional[str] = ..., full: _Optional[_Union[FullTrajectory, _Mapping]] = ..., upper_only: bool = ...) -> None: ...

class FullTrajectory(_message.Message):
    __slots__ = ("segments",)
    SEGMENTS_FIELD_NUMBER: _ClassVar[int]
    segments: _containers.RepeatedCompositeFieldContainer[JointSegment]
    def __init__(self, segments: _Optional[_Iterable[_Union[JointSegment, _Mapping]]] = ...) -> None: ...

class JointSegment(_message.Message):
    __slots__ = ("positions", "kp", "kd", "duration")
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    KP_FIELD_NUMBER: _ClassVar[int]
    KD_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    positions: _containers.RepeatedScalarFieldContainer[float]
    kp: _containers.RepeatedScalarFieldContainer[float]
    kd: _containers.RepeatedScalarFieldContainer[float]
    duration: float
    def __init__(self, positions: _Optional[_Iterable[float]] = ..., kp: _Optional[_Iterable[float]] = ..., kd: _Optional[_Iterable[float]] = ..., duration: _Optional[float] = ...) -> None: ...

class ModeCommand(_message.Message):
    __slots__ = ("mode",)
    MODE_FIELD_NUMBER: _ClassVar[int]
    mode: Mode
    def __init__(self, mode: _Optional[_Union[Mode, str]] = ...) -> None: ...

class EdgeTelemetry(_message.Message):
    __slots__ = ("timestamp_us", "fw_timestamp_us", "sequence", "fw_mode", "joint_pos", "joint_vel", "joint_current", "joint_temp", "imu_quat", "imu_gyro", "imu_gravity", "error_flags", "alert_count", "active_alerts", "fw_age_ms", "last_video_timestamp_us", "last_audio_timestamp_us", "applied_cmd")
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    FW_TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    FW_MODE_FIELD_NUMBER: _ClassVar[int]
    JOINT_POS_FIELD_NUMBER: _ClassVar[int]
    JOINT_VEL_FIELD_NUMBER: _ClassVar[int]
    JOINT_CURRENT_FIELD_NUMBER: _ClassVar[int]
    JOINT_TEMP_FIELD_NUMBER: _ClassVar[int]
    IMU_QUAT_FIELD_NUMBER: _ClassVar[int]
    IMU_GYRO_FIELD_NUMBER: _ClassVar[int]
    IMU_GRAVITY_FIELD_NUMBER: _ClassVar[int]
    ERROR_FLAGS_FIELD_NUMBER: _ClassVar[int]
    ALERT_COUNT_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_ALERTS_FIELD_NUMBER: _ClassVar[int]
    FW_AGE_MS_FIELD_NUMBER: _ClassVar[int]
    LAST_VIDEO_TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    LAST_AUDIO_TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    APPLIED_CMD_FIELD_NUMBER: _ClassVar[int]
    timestamp_us: int
    fw_timestamp_us: int
    sequence: int
    fw_mode: FirmwareMode
    joint_pos: _containers.RepeatedScalarFieldContainer[float]
    joint_vel: _containers.RepeatedScalarFieldContainer[float]
    joint_current: _containers.RepeatedScalarFieldContainer[float]
    joint_temp: _containers.RepeatedScalarFieldContainer[float]
    imu_quat: _containers.RepeatedScalarFieldContainer[float]
    imu_gyro: _containers.RepeatedScalarFieldContainer[float]
    imu_gravity: _containers.RepeatedScalarFieldContainer[float]
    error_flags: int
    alert_count: int
    active_alerts: _containers.RepeatedCompositeFieldContainer[FirmwareAlert]
    fw_age_ms: int
    last_video_timestamp_us: int
    last_audio_timestamp_us: int
    applied_cmd: AppliedCommandTiming
    def __init__(self, timestamp_us: _Optional[int] = ..., fw_timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., fw_mode: _Optional[_Union[FirmwareMode, str]] = ..., joint_pos: _Optional[_Iterable[float]] = ..., joint_vel: _Optional[_Iterable[float]] = ..., joint_current: _Optional[_Iterable[float]] = ..., joint_temp: _Optional[_Iterable[float]] = ..., imu_quat: _Optional[_Iterable[float]] = ..., imu_gyro: _Optional[_Iterable[float]] = ..., imu_gravity: _Optional[_Iterable[float]] = ..., error_flags: _Optional[int] = ..., alert_count: _Optional[int] = ..., active_alerts: _Optional[_Iterable[_Union[FirmwareAlert, _Mapping]]] = ..., fw_age_ms: _Optional[int] = ..., last_video_timestamp_us: _Optional[int] = ..., last_audio_timestamp_us: _Optional[int] = ..., applied_cmd: _Optional[_Union[AppliedCommandTiming, _Mapping]] = ...) -> None: ...

class AppliedCommandTiming(_message.Message):
    __slots__ = ("seq", "recv_us", "proc_us", "age_us")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    RECV_US_FIELD_NUMBER: _ClassVar[int]
    PROC_US_FIELD_NUMBER: _ClassVar[int]
    AGE_US_FIELD_NUMBER: _ClassVar[int]
    seq: int
    recv_us: int
    proc_us: int
    age_us: int
    def __init__(self, seq: _Optional[int] = ..., recv_us: _Optional[int] = ..., proc_us: _Optional[int] = ..., age_us: _Optional[int] = ...) -> None: ...

class EdgeEvent(_message.Message):
    __slots__ = ("timestamp_us", "sequence", "error", "diagnostics", "controller")
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    CONTROLLER_FIELD_NUMBER: _ClassVar[int]
    timestamp_us: int
    sequence: int
    error: EdgeError
    diagnostics: EdgeDiagnostics
    controller: ControllerEvent
    def __init__(self, timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., error: _Optional[_Union[EdgeError, _Mapping]] = ..., diagnostics: _Optional[_Union[EdgeDiagnostics, _Mapping]] = ..., controller: _Optional[_Union[ControllerEvent, _Mapping]] = ...) -> None: ...

class EdgeError(_message.Message):
    __slots__ = ("subsystem", "code", "message")
    SUBSYSTEM_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    subsystem: Subsystem
    code: str
    message: str
    def __init__(self, subsystem: _Optional[_Union[Subsystem, str]] = ..., code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class EdgeDiagnostics(_message.Message):
    __slots__ = ("can_health", "onnx_avg_ms", "onnx_max_ms", "onnx_count", "controller", "provisioned", "cloud_connected", "ble_connected", "camera_active", "mic_active")
    CAN_HEALTH_FIELD_NUMBER: _ClassVar[int]
    ONNX_AVG_MS_FIELD_NUMBER: _ClassVar[int]
    ONNX_MAX_MS_FIELD_NUMBER: _ClassVar[int]
    ONNX_COUNT_FIELD_NUMBER: _ClassVar[int]
    CONTROLLER_FIELD_NUMBER: _ClassVar[int]
    PROVISIONED_FIELD_NUMBER: _ClassVar[int]
    CLOUD_CONNECTED_FIELD_NUMBER: _ClassVar[int]
    BLE_CONNECTED_FIELD_NUMBER: _ClassVar[int]
    CAMERA_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    MIC_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    can_health: _containers.RepeatedCompositeFieldContainer[CanBusHealth]
    onnx_avg_ms: float
    onnx_max_ms: float
    onnx_count: int
    controller: str
    provisioned: bool
    cloud_connected: bool
    ble_connected: bool
    camera_active: bool
    mic_active: bool
    def __init__(self, can_health: _Optional[_Iterable[_Union[CanBusHealth, _Mapping]]] = ..., onnx_avg_ms: _Optional[float] = ..., onnx_max_ms: _Optional[float] = ..., onnx_count: _Optional[int] = ..., controller: _Optional[str] = ..., provisioned: bool = ..., cloud_connected: bool = ..., ble_connected: bool = ..., camera_active: bool = ..., mic_active: bool = ...) -> None: ...

class FirmwareAlert(_message.Message):
    __slots__ = ("id", "severity", "value", "threshold", "first_set_us", "source_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    FIRST_SET_US_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    severity: int
    value: int
    threshold: int
    first_set_us: int
    source_id: int
    def __init__(self, id: _Optional[int] = ..., severity: _Optional[int] = ..., value: _Optional[int] = ..., threshold: _Optional[int] = ..., first_set_us: _Optional[int] = ..., source_id: _Optional[int] = ...) -> None: ...

class CanBusHealth(_message.Message):
    __slots__ = ("frames_sent", "frames_received", "errors", "bus_offs")
    FRAMES_SENT_FIELD_NUMBER: _ClassVar[int]
    FRAMES_RECEIVED_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    BUS_OFFS_FIELD_NUMBER: _ClassVar[int]
    frames_sent: int
    frames_received: int
    errors: int
    bus_offs: int
    def __init__(self, frames_sent: _Optional[int] = ..., frames_received: _Optional[int] = ..., errors: _Optional[int] = ..., bus_offs: _Optional[int] = ...) -> None: ...

class ControllerEvent(_message.Message):
    __slots__ = ("previous", "current", "reason")
    PREVIOUS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    previous: str
    current: str
    reason: str
    def __init__(self, previous: _Optional[str] = ..., current: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...
