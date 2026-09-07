"""The generated ``asimov.io`` bindings, from wherever they are installed.

Two sources, one rule: an installed ``asimov-protocol`` package wins (the edge and its tools
import that one, and protobuf's descriptor pool tolerates exactly one copy of each ``.proto``
per process); the tree vendored under ``asimov_sdk/_vendor`` is the fallback that makes
``pip install asimov-sdk`` work with no access to the protocol repository. Both are the same
generated code at the same tag — see ``_vendor/VENDORED.md``.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["asimov-protocol", "vendored"]


@dataclass(frozen=True, slots=True)
class Bindings:
    command: Any  # asimov_command_pb2
    common: Any  # asimov_common_pb2
    state: Any  # asimov_state_pb2
    source: Source


@functools.cache
def load() -> Bindings:
    """Import the bindings lazily (protobuf loads when a transport opens, not at import)."""
    try:
        from asimov_protocol.v1 import asimov_command_pb2 as cmd
        from asimov_protocol.v1 import asimov_common_pb2 as common
        from asimov_protocol.v1 import asimov_state_pb2 as st

        return Bindings(cmd, common, st, "asimov-protocol")
    except ImportError:
        from asimov_sdk._vendor.asimov_protocol.v1 import asimov_command_pb2 as cmd
        from asimov_sdk._vendor.asimov_protocol.v1 import asimov_common_pb2 as common
        from asimov_sdk._vendor.asimov_protocol.v1 import asimov_state_pb2 as st

        return Bindings(cmd, common, st, "vendored")
