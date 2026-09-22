from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from typing import ClassVar as _ClassVar

DESCRIPTOR: _descriptor.FileDescriptor

class ControlMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTROL_MODE_DAMP: _ClassVar[ControlMode]
    CONTROL_MODE_STAND: _ClassVar[ControlMode]
    CONTROL_MODE_MOVE: _ClassVar[ControlMode]

class CommandControl(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COMMAND_CONTROL_POLICY: _ClassVar[CommandControl]
    COMMAND_CONTROL_TRAJECTORY: _ClassVar[CommandControl]

class EolAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EOL_MOVE_MOTOR: _ClassVar[EolAction]
    EOL_ZERO_ENCODER: _ClassVar[EolAction]
    EOL_SET_CAN_ID: _ClassVar[EolAction]
    EOL_FINALIZE: _ClassVar[EolAction]
CONTROL_MODE_DAMP: ControlMode
CONTROL_MODE_STAND: ControlMode
CONTROL_MODE_MOVE: ControlMode
COMMAND_CONTROL_POLICY: CommandControl
COMMAND_CONTROL_TRAJECTORY: CommandControl
EOL_MOVE_MOTOR: EolAction
EOL_ZERO_ENCODER: EolAction
EOL_SET_CAN_ID: EolAction
EOL_FINALIZE: EolAction
