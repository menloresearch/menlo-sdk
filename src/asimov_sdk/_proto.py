"""The generated ``asimov.io`` bindings, from wherever they are installed.

Two sources, one rule: an installed ``asimov-protocol`` package wins (the edge and its tools
import that one, and protobuf's descriptor pool tolerates exactly one copy of each ``.proto``
per process); the tree vendored under ``asimov_sdk/_vendor`` is the fallback that makes
installing the wheel work with no access to the protocol repository. Both are the same
generated code at the same tag — see ``_vendor/VENDORED.md``.
"""

from __future__ import annotations

import filecmp
import functools
import importlib
import importlib.util
import logging
import sys
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


log = logging.getLogger(__name__)


_VENDORED_V1 = Path(__file__).parent / "_vendor" / "asimov_protocol" / "v1"


def _same_as_vendored(package: str) -> bool:
    """True when an installed ``package`` is byte-for-byte the tree this SDK was built
    against — checked on the FILESYSTEM, without importing anything. Importing even the
    package's ``__init__`` pulls in every generated module, and each registers its
    ``.proto`` in protobuf's process-global descriptor pool; importing one tree and then
    falling back to the other would register the same file twice and crash. So the
    decision is made first, then exactly one tree is imported.

    Identity, not mere completeness: a different asimov-protocol release would silently
    change what the SDK decodes while ``robots.PROTOCOL_VERSION`` still said otherwise."""
    spec = importlib.util.find_spec(package)  # top-level lookup: resolves, does not import
    if spec is None or not spec.submodule_search_locations:
        return False
    generated = [q for q in _VENDORED_V1.glob("*.py") if q.name != "__init__.py"]
    for loc in spec.submodule_search_locations:
        v1 = Path(loc) / "v1"
        if not (v1 / "__init__.py").is_file():
            continue
        if all(
            (v1 / q.name).is_file() and filecmp.cmp(v1 / q.name, q, shallow=False)
            for q in generated
        ):
            return True
        log.warning(
            "an installed asimov_protocol package differs from the bindings this SDK was "
            "built against; using the vendored copy. Do not import both in one process."
        )
        return False
    return False


def _already_imported(package: str) -> bool:
    return any(f"{package}.v1.{m}" in sys.modules for m in _MODULES)


@functools.cache
def load() -> Bindings:
    """Import the bindings lazily (protobuf loads when a transport opens, not at import)."""
    if _already_imported("asimov_protocol"):
        # Someone in this process imported the installed tree first. Its descriptors are
        # registered; importing the vendored copy now would register them twice and crash.
        if not _same_as_vendored("asimov_protocol"):
            log.warning(
                "asimov_protocol was imported before the SDK and differs from the bindings the "
                "SDK was built against; using the imported tree to keep one descriptor set."
            )
        package, source = "asimov_protocol", "asimov-protocol"
    elif _same_as_vendored("asimov_protocol"):
        package, source = "asimov_protocol", "asimov-protocol"
    else:
        package, source = "asimov_sdk._vendor.asimov_protocol", "vendored"
    cmd, common, st = (importlib.import_module(f"{package}.v1.{m}") for m in _MODULES)
    return Bindings(cmd, common, st, source)  # type: ignore[arg-type]
