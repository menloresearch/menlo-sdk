"""The generated ``asimov.io`` bindings, from the ``asimov-protocol`` package.

One source. ``asimov-protocol`` is a declared dependency (``asimov-protocol>=1.2,<2`` in
``pyproject.toml``); the edge, its tools and this SDK all import the same installed tree,
which keeps protobuf's process-global descriptor pool to exactly one copy of each ``.proto``.
The upper bound follows the protocol's MAJOR, which tracks the wire directory (``v1/``).
"""

from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["asimov-protocol"]


@dataclass(frozen=True, slots=True)
class Bindings:
    command: Any  # asimov_command_pb2
    common: Any  # asimov_common_pb2
    state: Any  # asimov_state_pb2
    source: Source


_MODULES = ("asimov_command_pb2", "asimov_common_pb2", "asimov_state_pb2")


@functools.cache
def load() -> Bindings:
    """Import the bindings lazily (protobuf loads when a transport opens, not at import)."""
    cmd, common, st = (importlib.import_module(f"asimov_protocol.v1.{m}") for m in _MODULES)
    return Bindings(cmd, common, st, "asimov-protocol")
