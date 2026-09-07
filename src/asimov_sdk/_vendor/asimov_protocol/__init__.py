"""Asimov robot protocol buffers (generated package).

Usage:
    from asimov_protocol.v1 import asimov_command_pb2, asimov_common_pb2

    cmd = asimov_command_pb2.RobotCommand()
    cmd.mode = asimov_common_pb2.CONTROL_MODE_MOVE
"""

from . import v1

__version__ = "1.0.0"
__all__ = ["v1"]
