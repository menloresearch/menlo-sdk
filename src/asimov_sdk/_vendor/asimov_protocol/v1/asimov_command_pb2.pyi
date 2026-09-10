from . import asimov_common_pb2 as _asimov_common_pb2
from . import nanopb_pb2 as _nanopb_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class VelocityCommand(_message.Message):
    __slots__ = ("vx", "vy", "vyaw")
    VX_FIELD_NUMBER: _ClassVar[int]
    VY_FIELD_NUMBER: _ClassVar[int]
    VYAW_FIELD_NUMBER: _ClassVar[int]
    vx: float
    vy: float
    vyaw: float
    def __init__(self, vx: _Optional[float] = ..., vy: _Optional[float] = ..., vyaw: _Optional[float] = ...) -> None: ...

class AllTrajectory(_message.Message):
    __slots__ = ("positions", "kp", "kd")
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    KP_FIELD_NUMBER: _ClassVar[int]
    KD_FIELD_NUMBER: _ClassVar[int]
    positions: _containers.RepeatedScalarFieldContainer[float]
    kp: _containers.RepeatedScalarFieldContainer[float]
    kd: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, positions: _Optional[_Iterable[float]] = ..., kp: _Optional[_Iterable[float]] = ..., kd: _Optional[_Iterable[float]] = ...) -> None: ...

class EolCommand(_message.Message):
    __slots__ = ("action", "motor_id", "position", "kp", "kd", "new_can_id")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    KP_FIELD_NUMBER: _ClassVar[int]
    KD_FIELD_NUMBER: _ClassVar[int]
    NEW_CAN_ID_FIELD_NUMBER: _ClassVar[int]
    action: _asimov_common_pb2.EolAction
    motor_id: int
    position: float
    kp: float
    kd: float
    new_can_id: int
    def __init__(self, action: _Optional[_Union[_asimov_common_pb2.EolAction, str]] = ..., motor_id: _Optional[int] = ..., position: _Optional[float] = ..., kp: _Optional[float] = ..., kd: _Optional[float] = ..., new_can_id: _Optional[int] = ...) -> None: ...

class RobotCommand(_message.Message):
    __slots__ = ("mode", "command_control", "policy", "all_trajectory", "eol", "timestamp_us", "sequence", "protocol_version")
    MODE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_CONTROL_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    ALL_TRAJECTORY_FIELD_NUMBER: _ClassVar[int]
    EOL_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    mode: _asimov_common_pb2.ControlMode
    command_control: _asimov_common_pb2.CommandControl
    policy: VelocityCommand
    all_trajectory: AllTrajectory
    eol: EolCommand
    timestamp_us: int
    sequence: int
    protocol_version: int
    def __init__(self, mode: _Optional[_Union[_asimov_common_pb2.ControlMode, str]] = ..., command_control: _Optional[_Union[_asimov_common_pb2.CommandControl, str]] = ..., policy: _Optional[_Union[VelocityCommand, _Mapping]] = ..., all_trajectory: _Optional[_Union[AllTrajectory, _Mapping]] = ..., eol: _Optional[_Union[EolCommand, _Mapping]] = ..., timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., protocol_version: _Optional[int] = ...) -> None: ...
