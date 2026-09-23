from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EolCommand(_message.Message):
    __slots__ = ("timestamp_us", "sequence", "zero_motor", "set_motor_id", "imu_test", "media_test", "eol_complete")
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    ZERO_MOTOR_FIELD_NUMBER: _ClassVar[int]
    SET_MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    IMU_TEST_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TEST_FIELD_NUMBER: _ClassVar[int]
    EOL_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    timestamp_us: int
    sequence: int
    zero_motor: ZeroMotorCommand
    set_motor_id: SetMotorIdCommand
    imu_test: ImuTestCommand
    media_test: MediaTestCommand
    eol_complete: EolCompleteCommand
    def __init__(self, timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., zero_motor: _Optional[_Union[ZeroMotorCommand, _Mapping]] = ..., set_motor_id: _Optional[_Union[SetMotorIdCommand, _Mapping]] = ..., imu_test: _Optional[_Union[ImuTestCommand, _Mapping]] = ..., media_test: _Optional[_Union[MediaTestCommand, _Mapping]] = ..., eol_complete: _Optional[_Union[EolCompleteCommand, _Mapping]] = ...) -> None: ...

class ZeroMotorCommand(_message.Message):
    __slots__ = ("target", "can_bus", "motor_id")
    class Target(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        ALL: _ClassVar[ZeroMotorCommand.Target]
        SINGLE: _ClassVar[ZeroMotorCommand.Target]
    ALL: ZeroMotorCommand.Target
    SINGLE: ZeroMotorCommand.Target
    TARGET_FIELD_NUMBER: _ClassVar[int]
    CAN_BUS_FIELD_NUMBER: _ClassVar[int]
    MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    target: ZeroMotorCommand.Target
    can_bus: int
    motor_id: int
    def __init__(self, target: _Optional[_Union[ZeroMotorCommand.Target, str]] = ..., can_bus: _Optional[int] = ..., motor_id: _Optional[int] = ...) -> None: ...

class SetMotorIdCommand(_message.Message):
    __slots__ = ("can_bus", "new_motor_id")
    CAN_BUS_FIELD_NUMBER: _ClassVar[int]
    NEW_MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    can_bus: int
    new_motor_id: int
    def __init__(self, can_bus: _Optional[int] = ..., new_motor_id: _Optional[int] = ...) -> None: ...

class ImuTestCommand(_message.Message):
    __slots__ = ("action",)
    class Action(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        START: _ClassVar[ImuTestCommand.Action]
        STOP: _ClassVar[ImuTestCommand.Action]
    START: ImuTestCommand.Action
    STOP: ImuTestCommand.Action
    ACTION_FIELD_NUMBER: _ClassVar[int]
    action: ImuTestCommand.Action
    def __init__(self, action: _Optional[_Union[ImuTestCommand.Action, str]] = ...) -> None: ...

class MediaTestCommand(_message.Message):
    __slots__ = ("action",)
    class Action(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        START_CAMERA: _ClassVar[MediaTestCommand.Action]
        STOP_CAMERA: _ClassVar[MediaTestCommand.Action]
        START_AUDIO: _ClassVar[MediaTestCommand.Action]
        STOP_AUDIO: _ClassVar[MediaTestCommand.Action]
    START_CAMERA: MediaTestCommand.Action
    STOP_CAMERA: MediaTestCommand.Action
    START_AUDIO: MediaTestCommand.Action
    STOP_AUDIO: MediaTestCommand.Action
    ACTION_FIELD_NUMBER: _ClassVar[int]
    action: MediaTestCommand.Action
    def __init__(self, action: _Optional[_Union[MediaTestCommand.Action, str]] = ...) -> None: ...

class EolCompleteCommand(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class EolResult(_message.Message):
    __slots__ = ("timestamp_us", "sequence", "success", "error", "zero_motor", "set_motor_id", "imu_test", "media_test", "eol_complete")
    TIMESTAMP_US_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ZERO_MOTOR_FIELD_NUMBER: _ClassVar[int]
    SET_MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    IMU_TEST_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TEST_FIELD_NUMBER: _ClassVar[int]
    EOL_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    timestamp_us: int
    sequence: int
    success: bool
    error: str
    zero_motor: ZeroMotorResult
    set_motor_id: SetMotorIdResult
    imu_test: ImuTestResult
    media_test: MediaTestResult
    eol_complete: EolCompleteResult
    def __init__(self, timestamp_us: _Optional[int] = ..., sequence: _Optional[int] = ..., success: bool = ..., error: _Optional[str] = ..., zero_motor: _Optional[_Union[ZeroMotorResult, _Mapping]] = ..., set_motor_id: _Optional[_Union[SetMotorIdResult, _Mapping]] = ..., imu_test: _Optional[_Union[ImuTestResult, _Mapping]] = ..., media_test: _Optional[_Union[MediaTestResult, _Mapping]] = ..., eol_complete: _Optional[_Union[EolCompleteResult, _Mapping]] = ...) -> None: ...

class ZeroMotorResult(_message.Message):
    __slots__ = ("motors_zeroed", "failed_motors")
    MOTORS_ZEROED_FIELD_NUMBER: _ClassVar[int]
    FAILED_MOTORS_FIELD_NUMBER: _ClassVar[int]
    motors_zeroed: int
    failed_motors: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, motors_zeroed: _Optional[int] = ..., failed_motors: _Optional[_Iterable[int]] = ...) -> None: ...

class SetMotorIdResult(_message.Message):
    __slots__ = ("can_bus", "motor_id")
    CAN_BUS_FIELD_NUMBER: _ClassVar[int]
    MOTOR_ID_FIELD_NUMBER: _ClassVar[int]
    can_bus: int
    motor_id: int
    def __init__(self, can_bus: _Optional[int] = ..., motor_id: _Optional[int] = ...) -> None: ...

class ImuTestResult(_message.Message):
    __slots__ = ("quat", "gravity", "gyro", "temperature", "calibrated")
    QUAT_FIELD_NUMBER: _ClassVar[int]
    GRAVITY_FIELD_NUMBER: _ClassVar[int]
    GYRO_FIELD_NUMBER: _ClassVar[int]
    TEMPERATURE_FIELD_NUMBER: _ClassVar[int]
    CALIBRATED_FIELD_NUMBER: _ClassVar[int]
    quat: _containers.RepeatedScalarFieldContainer[float]
    gravity: _containers.RepeatedScalarFieldContainer[float]
    gyro: _containers.RepeatedScalarFieldContainer[float]
    temperature: float
    calibrated: bool
    def __init__(self, quat: _Optional[_Iterable[float]] = ..., gravity: _Optional[_Iterable[float]] = ..., gyro: _Optional[_Iterable[float]] = ..., temperature: _Optional[float] = ..., calibrated: bool = ...) -> None: ...

class MediaTestResult(_message.Message):
    __slots__ = ("camera_ok", "camera_width", "camera_height", "camera_fps", "audio_capture_ok", "audio_playback_ok")
    CAMERA_OK_FIELD_NUMBER: _ClassVar[int]
    CAMERA_WIDTH_FIELD_NUMBER: _ClassVar[int]
    CAMERA_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    CAMERA_FPS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_CAPTURE_OK_FIELD_NUMBER: _ClassVar[int]
    AUDIO_PLAYBACK_OK_FIELD_NUMBER: _ClassVar[int]
    camera_ok: bool
    camera_width: int
    camera_height: int
    camera_fps: float
    audio_capture_ok: bool
    audio_playback_ok: bool
    def __init__(self, camera_ok: bool = ..., camera_width: _Optional[int] = ..., camera_height: _Optional[int] = ..., camera_fps: _Optional[float] = ..., audio_capture_ok: bool = ..., audio_playback_ok: bool = ...) -> None: ...

class EolCompleteResult(_message.Message):
    __slots__ = ("flag_set",)
    FLAG_SET_FIELD_NUMBER: _ClassVar[int]
    flag_set: bool
    def __init__(self, flag_set: bool = ...) -> None: ...
