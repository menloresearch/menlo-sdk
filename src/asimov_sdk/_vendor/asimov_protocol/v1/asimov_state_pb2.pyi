from . import asimov_common_pb2 as _asimov_common_pb2
from . import asimov_diagnostics_pb2 as _asimov_diagnostics_pb2
from . import nanopb_pb2 as _nanopb_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RobotState(_message.Message):
    __slots__ = ("timestamp_us", "sequence", "current_mode", "joint_pos", "joint_vel", "joint_current", "joint_temp", "base_ang_vel", "projected_gravity", "base_quat", "error_flags", "last_cmd_sequence", "active_alerts", "policy_timing", "onnx_metrics", "can_health", "protocol_version", "eol_mode", "eol_status", "eol_complete", "battery")
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    CURRENT_MODE_FIELD_NUMBER: _ClassVar[int]
    JOINT_POS_FIELD_NUMBER: _ClassVar[int]
    JOINT_VEL_FIELD_NUMBER: _ClassVar[int]
    JOINT_CURRENT_FIELD_NUMBER: _ClassVar[int]
    JOINT_TEMP_FIELD_NUMBER: _ClassVar[int]
    BASE_ANG_VEL_FIELD_NUMBER: _ClassVar[int]
    PROJECTED_GRAVITY_FIELD_NUMBER: _ClassVar[int]
    BASE_QUAT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FLAGS_FIELD_NUMBER: _ClassVar[int]
    LAST_CMD_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_ALERTS_FIELD_NUMBER: _ClassVar[int]
    POLICY_TIMING_FIELD_NUMBER: _ClassVar[int]
    ONNX_METRICS_FIELD_NUMBER: _ClassVar[int]
    CAN_HEALTH_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    EOL_MODE_FIELD_NUMBER: _ClassVar[int]
    EOL_STATUS_FIELD_NUMBER: _ClassVar[int]
    EOL_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    BATTERY_FIELD_NUMBER: _ClassVar[int]
    timestamp_us: int
    sequence: int
    current_mode: _asimov_common_pb2.ControlMode
    joint_pos: _containers.RepeatedScalarFieldContainer[float]
    joint_vel: _containers.RepeatedScalarFieldContainer[float]
    joint_current: _containers.RepeatedScalarFieldContainer[float]
    joint_temp: _containers.RepeatedScalarFieldContainer[float]
    base_ang_vel: _containers.RepeatedScalarFieldContainer[float]
    projected_gravity: _containers.RepeatedScalarFieldContainer[float]
    base_quat: _containers.RepeatedScalarFieldContainer[float]
    error_flags: int
    last_cmd_sequence: int
    active_alerts: _containers.RepeatedCompositeFieldContainer[_asimov_diagnostics_pb2.Alert]
    policy_timing: _asimov_diagnostics_pb2.ThreadMetrics
    onnx_metrics: _asimov_diagnostics_pb2.OnnxMetrics
    can_health: _containers.RepeatedCompositeFieldContainer[_asimov_diagnostics_pb2.CanMetrics]
    protocol_version: int
    eol_mode: bool
    eol_status: EolMotorStatus
    eol_complete: bool
    battery: BatteryState
    def __init__(self, timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., current_mode: _Optional[_Union[_asimov_common_pb2.ControlMode, str]] = ..., joint_pos: _Optional[_Iterable[float]] = ..., joint_vel: _Optional[_Iterable[float]] = ..., joint_current: _Optional[_Iterable[float]] = ..., joint_temp: _Optional[_Iterable[float]] = ..., base_ang_vel: _Optional[_Iterable[float]] = ..., projected_gravity: _Optional[_Iterable[float]] = ..., base_quat: _Optional[_Iterable[float]] = ..., error_flags: _Optional[int] = ..., last_cmd_sequence: _Optional[int] = ..., active_alerts: _Optional[_Iterable[_Union[_asimov_diagnostics_pb2.Alert, _Mapping]]] = ..., policy_timing: _Optional[_Union[_asimov_diagnostics_pb2.ThreadMetrics, _Mapping]] = ..., onnx_metrics: _Optional[_Union[_asimov_diagnostics_pb2.OnnxMetrics, _Mapping]] = ..., can_health: _Optional[_Iterable[_Union[_asimov_diagnostics_pb2.CanMetrics, _Mapping]]] = ..., protocol_version: _Optional[int] = ..., eol_mode: bool = ..., eol_status: _Optional[_Union[EolMotorStatus, _Mapping]] = ..., eol_complete: bool = ..., battery: _Optional[_Union[BatteryState, _Mapping]] = ...) -> None: ...

class BatteryState(_message.Message):
    __slots__ = ("voltage_v", "current_a", "soc_percent", "max_cell_temp_c", "protection_flags")
    VOLTAGE_V_FIELD_NUMBER: _ClassVar[int]
    CURRENT_A_FIELD_NUMBER: _ClassVar[int]
    SOC_PERCENT_FIELD_NUMBER: _ClassVar[int]
    MAX_CELL_TEMP_C_FIELD_NUMBER: _ClassVar[int]
    PROTECTION_FLAGS_FIELD_NUMBER: _ClassVar[int]
    voltage_v: float
    current_a: float
    soc_percent: float
    max_cell_temp_c: float
    protection_flags: int
    def __init__(self, voltage_v: _Optional[float] = ..., current_a: _Optional[float] = ..., soc_percent: _Optional[float] = ..., max_cell_temp_c: _Optional[float] = ..., protection_flags: _Optional[int] = ...) -> None: ...

class EolMotorStatus(_message.Message):
    __slots__ = ("motor_id", "position", "velocity", "current", "temperature", "error_code", "motor_name")
    MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    VELOCITY_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FIELD_NUMBER: _ClassVar[int]
    TEMPERATURE_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    MOTOR_NAME_FIELD_NUMBER: _ClassVar[int]
    motor_id: int
    position: float
    velocity: float
    current: float
    temperature: float
    error_code: int
    motor_name: str
    def __init__(self, motor_id: _Optional[int] = ..., position: _Optional[float] = ..., velocity: _Optional[float] = ..., current: _Optional[float] = ..., temperature: _Optional[float] = ..., error_code: _Optional[int] = ..., motor_name: _Optional[str] = ...) -> None: ...
