"""The generated ``asimov.io`` bindings, from wherever they are installed.

Two sources, one rule: an installed ``asimov-protocol`` package wins (the edge and its tools
import that one, and protobuf's descriptor pool tolerates exactly one copy of each ``.proto``
per process); the tree vendored under ``asimov_sdk/_vendor`` is the fallback that makes
``pip install asimov-sdk`` work with no access to the protocol repository. Both are the same
generated code at the same tag — see ``_vendor/VENDORED.md``.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Source = Literal["asimov-protocol", "vendored"]


@dataclass(frozen=True, slots=True)
class Bindings:
    command: Any  # asimov_command_pb2
    common: Any  # asimov_common_pb2
    state: Any  # asimov_state_pb2
    source: Source


_MODULES = ("asimov_command_pb2", "asimov_common_pb2", "asimov_state_pb2")


def _complete(package: str) -> bool:
    """True when every module we need exists in ``package`` — checked on the FILESYSTEM,
    without importing anything. Importing even the package's ``__init__`` can pull in the
    generated modules, and each of those registers its ``.proto`` in protobuf's process-global
    descriptor pool; importing one tree and then falling back to the other would register the
    same file twice and crash. So the decision is made first, then exactly one tree is imported."""
    spec = importlib.util.find_spec(package)  # top-level lookup: resolves, does not import
    if spec is None or not spec.submodule_search_locations:
        return False
    roots = [Path(loc) / "v1" for loc in spec.submodule_search_locations]
    return all(any((root / f"{m}.py").is_file() for root in roots) for m in _MODULES)


@functools.cache
def load() -> Bindings:
    """Import the bindings lazily (protobuf loads when a transport opens, not at import)."""
    if _complete("asimov_protocol"):
        package, source = "asimov_protocol", "asimov-protocol"
    else:
        package, source = "asimov_sdk._vendor.asimov_protocol", "vendored"
    cmd, common, st = (importlib.import_module(f"{package}.v1.{m}") for m in _MODULES)
    return Bindings(cmd, common, st, source)  # type: ignore[arg-type]
